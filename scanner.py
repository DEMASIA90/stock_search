from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import time
import traceback
import zipfile
from collections import Counter
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MORNING_INVEST_COMPONENT_VERSION = "13.2"

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from universe import Stock, fetch_kr_restricted_symbols, fetch_us_halted_symbols, get_universe
from supertrend_strategy import analyze as analyze_supertrend, OPINION_ORDER

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FX_CACHE_FILE = DATA_DIR / "fx_usdkrw.json"

# -----------------------------------------------------------------------------
# Dongtan Trading Center (DTC) scanner v13.2 · Supertrend Strategy
# -----------------------------------------------------------------------------
# Opinion engine: Supertrend(period=10, multiplier=2) only.
#   P0 = Supertrend value on the bar immediately BEFORE the latest DOWN -> UP transition
#   P1 = current Supertrend value
#   SELL = current Supertrend direction DOWN
#   BUY  = current direction UP and P1 >= P0, graded by current Close vs P0
#          S <2%, A <5%, B <10%, C <20%; otherwise HOLD.
# Ranking: Buy S -> Buy A -> Buy B -> Buy C -> Hold -> Sell, then market size.
# Backtest: 2Y, first Buy S per rising leg, next-open entry/exit, costs + grade diagnostics.
# Chart: ~6 months adjusted real OHLC candles + Supertrend(10,2).
# -----------------------------------------------------------------------------

FULL_HISTORY_CALENDAR_DAYS = 1120
# Both FULL and QUICK need >=604 valid sessions: 2Y backtest (~504) + 100-bar
# post-ATR warm-up discard. 980 calendar days leaves a holiday buffer.
QUICK_HISTORY_CALENDAR_DAYS = 980
BATCH_SIZE = 48
RETRY_BATCH_SIZE = 8
DOWNLOAD_THREADS = 8
# Successful batches are throttled only periodically (see scan loop) rather than
# after every request. Retries remain deliberately slower to avoid a 429 cascade.
PRIMARY_BATCH_SLEEP = (0.10, 0.28)
RETRY_BATCH_SLEEP = (0.70, 1.35)
RETRY_ATTEMPTS = 2

QUIZ_MIN_MARKET_SIZE_KRW = 100_000_000_000_000.0
QUIZ_HISTORY_POINTS = 620
QUIZ_MIN_POINTS = 140
NAVER_ETF_LIST_URL = "https://finance.naver.com/api/sise/etfItemList.nhn"
KRX_JSON_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_STOCK_LOADER_URL = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101"
NAVER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Referer": "https://finance.naver.com/sise/etf.naver",
}
NAVER_ETF_MARKET_SUM_UNIT_KRW = 100_000_000.0  # marketSum is reported in KRW 100M units (억원)

MIN_TRADING_DAYS = 604  # required by SuperTrend spec: ~504 test bars + 100 discarded warm-up bars
DISPLAY_META_TOP_N = 100
MIN_PRICE_KRW = 1_000.0
MIN_MARKET_SIZE_KRW = 10_000_000_000_000.0  # equities only, inherited universe rule
ETF_CATEGORIES = {"KR_ETF", "US_ETF"}

# A symbol that survives the official exchange filters but still has no Yahoo
# daily data after a healthy FULL scan is temporarily quarantined. This stops
# the same stale/mismapped ticker from producing 404s on every intraday run,
# while automatically retrying it the next day.
YAHOO_QUARANTINE_HOURS = 20
YAHOO_QUARANTINE_MAX_RATIO = 0.01
YAHOO_QUARANTINE_MIN_HEALTHY_COVERAGE = 0.97

# Slow metadata caches. Price scanning never waits on these for the whole universe;
# sector/ETF size enrichment runs only for the displayed top 100.
STOCK_SHARES_CACHE_DAYS = 30
DISPLAY_META_CACHE_DAYS = 45
MARKET_SIZE_RETRY_ATTEMPTS = 3
MARKET_SIZE_MIN_LOOKUP_COVERAGE = 0.90

MIN_COVERAGE = {
    "KR": 0.95,
    "KR_ETF": 0.95,
    "US": 0.95,
    "US_ETF": 0.95,
}

CATEGORY_DIR = {
    "KR": "kr",
    "KR_ETF": "kr-etf",
    "US": "us",
    "US_ETF": "us-etf",
}
UNIVERSE_CACHE_FILE = {
    "KR": "universe_kr.json",
    "KR_ETF": "universe_kr_etf.json",
    "US": "universe_us.json",
    "US_ETF": "universe_us_etf.json",
}
CATEGORY_LABEL = {
    "KR": "국장",
    "KR_ETF": "국장 ETF",
    "US": "미장",
    "US_ETF": "미장 ETF",
}
CATEGORY_TZ = {
    "KR": "Asia/Seoul",
    "KR_ETF": "Asia/Seoul",
    "US": "America/New_York",
    "US_ETF": "America/New_York",
}
CATEGORY_CLOSE = {
    "KR": dtime(15, 40),
    "KR_ETF": dtime(15, 40),
    "US": dtime(16, 15),
    "US_ETF": dtime(16, 15),
}


def finite(value, default=np.nan) -> float:
    try:
        v = float(value)
        return v if np.isfinite(v) else default
    except Exception:
        return default


def clean(value, digits=4):
    v = finite(value)
    return round(v, digits) if np.isfinite(v) else None


def _numeric_ohlc(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    out.index = pd.DatetimeIndex(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for col in ("Open", "High", "Low", "Close"):
        if col not in out.columns:
            return pd.DataFrame()
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "Adj Close" in out.columns:
        out["Adj Close"] = pd.to_numeric(out["Adj Close"], errors="coerce")
        # Defensive fallback for any non-auto-adjusted source: scale O/H/L/C by
        # Adj Close / Close so splits/dividends cannot distort historical ST.
        raw_close = out["Close"].replace(0, np.nan)
        factor = (out["Adj Close"] / raw_close).replace([np.inf, -np.inf], np.nan)
        if factor.notna().any():
            for col in ("Open", "High", "Low", "Close"):
                out[col] = out[col] * factor
    if "Volume" in out.columns:
        out["Volume"] = pd.to_numeric(out["Volume"], errors="coerce").fillna(0.0).clip(lower=0.0)
    else:
        out["Volume"] = 0.0
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    out = out[(out["Close"] > 0) & (out["High"] > 0) & (out["Low"] > 0)]
    return out


def completed_daily(frame: pd.DataFrame, category: str, include_active_day: bool = False) -> pd.DataFrame:
    if frame.empty or include_active_day:
        return frame
    now = datetime.now(ZoneInfo(CATEGORY_TZ[category]))
    if frame.index[-1].date() == now.date() and now.time().replace(tzinfo=None) < CATEGORY_CLOSE[category]:
        return frame.iloc[:-1]
    return frame


def thresholds_for(category: str, usdkrw: float | None) -> dict:
    if category in {"KR", "KR_ETF"}:
        return {"min_price": MIN_PRICE_KRW, "currency": "KRW", "usdkrw": None}
    if not usdkrw:
        raise RuntimeError("USD/KRW is required for US thresholds")
    return {"min_price": MIN_PRICE_KRW / usdkrw, "currency": "USD", "usdkrw": round(usdkrw, 4)}


def fetch_usdkrw() -> tuple[float, str]:
    try:
        raw = yf.download(
            "KRW=X",
            period="10d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=25,
        )
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw.xs("Close", axis=1, level=0).iloc[:, 0]
        else:
            close = raw["Close"]
        fx = finite(pd.to_numeric(close, errors="coerce").dropna().iloc[-1])
        if 500 <= fx <= 3000:
            FX_CACHE_FILE.write_text(json.dumps({
                "value": float(fx),
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": "yahoo",
            }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            return fx, "yahoo"
    except Exception as exc:
        print(f"USD/KRW lookup failed: {type(exc).__name__}: {exc}")

    try:
        cached = json.loads(FX_CACHE_FILE.read_text(encoding="utf-8")) if FX_CACHE_FILE.is_file() else {}
        cached_fx = finite(cached.get("value"))
        if 500 <= cached_fx <= 3000 and _age_days(cached.get("fetched_at")) <= 3:
            return cached_fx, "cache"
    except Exception:
        pass

    # Last-resort QUICK-mode fallback. FULL scans reject this source in main().
    return 1400.0, "fallback_1400"


def _size_cache_path(category: str) -> Path:
    return DATA_DIR / CATEGORY_DIR[category] / "sizes.json"


def _load_size_cache(category: str) -> dict:
    path = _size_cache_path(category)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _age_days(raw) -> float:
    if not raw:
        return 10_000.0
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    except Exception:
        return 10_000.0


def _refresh_kr_etf_size_cache_from_naver(size_cache: dict) -> int:
    """Prime KR ETF market caps in one request instead of 300 Yahoo metadata calls.

    Naver's legacy ETF list exposes marketSum in units of KRW 100 million. This
    is also more reliable for Korean ETF symbols than Yahoo totalAssets.
    """
    try:
        response = requests.get(NAVER_ETF_LIST_URL, headers=NAVER_HEADERS, timeout=25)
        response.raise_for_status()
        rows = ((response.json().get("result") or {}).get("etfItemList") or [])
    except Exception as exc:
        print(f"[KR_ETF] Naver market-size snapshot unavailable: {type(exc).__name__}: {exc}")
        return 0

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updated = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("itemcode") or "").strip().upper()
        code = code.zfill(6) if code.isdigit() else code
        raw_market_sum = str(row.get("marketSum") or "").replace(",", "").strip()
        market_sum_eok = finite(raw_market_sum)
        if not re.fullmatch(r"[0-9A-Z]{6}", code) or not np.isfinite(market_sum_eok) or market_sum_eok <= 0:
            continue

        native_size = float(market_sum_eok * NAVER_ETF_MARKET_SUM_UNIT_KRW)
        ticker = f"{code}.KS"
        entry = size_cache.get(ticker) if isinstance(size_cache.get(ticker), dict) else {}
        entry = dict(entry)
        entry.update({
            "basis": "market_cap",
            "value": native_size,
            "currency": "KRW",
            "fetched_at": now,
            "sector": "ETF",
            "meta_fetched_at": now,
            "display_size_native": native_size,
            "display_size_basis": "market_cap",
            "source": "naver_etf_marketSum",
        })
        size_cache[ticker] = entry
        updated += 1

    print(f"[KR_ETF] Naver market-size snapshot: {updated:,} ETFs")
    return updated


def _refresh_kr_equity_size_cache_from_krx(size_cache: dict, universe: list[Stock]) -> tuple[int, set[str]]:
    """Best-effort one-request KR stock market-cap snapshot.

    This avoids thousands of Yahoo metadata calls when KRX Data Marketplace is
    available. KRX occasionally blocks automated access, so failure is nonfatal
    and the existing Yahoo/cache path remains the fallback.
    """
    session = requests.Session()
    headers = {
        **NAVER_HEADERS,
        "Origin": "https://data.krx.co.kr",
        "Referer": KRX_STOCK_LOADER_URL,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    try:
        session.get(KRX_STOCK_LOADER_URL, headers=headers, timeout=20)
    except Exception:
        pass

    ticker_by_code = {str(s.symbol): s.ticker for s in universe if s.category == "KR"}
    now_kr = datetime.now(ZoneInfo("Asia/Seoul"))
    last_error = None
    for offset in range(0, 8):
        d = (now_kr.date() - timedelta(days=offset)).strftime("%Y%m%d")
        form = {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
            "locale": "ko_KR",
            "mktId": "ALL",
            "trdDd": d,
            "share": "1",
            "money": "1",
            "csvxls_isNo": "false",
        }
        try:
            response = session.post(KRX_JSON_URL, data=form, headers=headers, timeout=35)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("OutBlock_1") or payload.get("output") or []
            if not isinstance(rows, list) or len(rows) < 500:
                continue
            fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            updated = 0
            active_symbols: set[str] = set()
            for row in rows:
                code = re.sub(r"\D", "", str(row.get("ISU_SRT_CD") or row.get("ISU_CD") or ""))
                raw_cap = str(row.get("MKTCAP") or "").replace(",", "").strip()
                cap = finite(raw_cap)
                if not re.fullmatch(r"\d{6}", code) or not np.isfinite(cap) or cap <= 0:
                    continue
                # MDCSTAT01501 is the official current trading-date snapshot.
                # Keep this set even when the KIND name universe has no match; it
                # is used to remove stale/delisted KIND rows before Yahoo download.
                active_symbols.add(code)
                ticker = ticker_by_code.get(code)
                if not ticker:
                    continue
                old = size_cache.get(ticker) if isinstance(size_cache.get(ticker), dict) else {}
                entry = dict(old)
                entry.update({
                    "basis": "market_cap",
                    "value": float(cap),
                    "currency": "KRW",
                    "fetched_at": fetched_at,
                    "display_size_native": float(cap),
                    "display_size_basis": "market_cap",
                    "source": "krx_MDCSTAT01501",
                })
                size_cache[ticker] = entry
                updated += 1
            if updated >= 500 and len(active_symbols) >= 500:
                print(f"[KR] KRX market-cap snapshot {d}: {updated:,} matched stocks / {len(active_symbols):,} active codes")
                return updated, active_symbols
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        print(f"[KR] KRX bulk market-cap unavailable; Yahoo/cache fallback: {type(last_error).__name__}: {last_error}")
    return 0, set()


def _fetch_stock_size_basis(stock: Stock) -> dict | None:
    ticker = yf.Ticker(stock.ticker)
    if stock.category in ETF_CATEGORIES:
        # ETF filters need a size value for the full searchable universe, not only
        # the visible cards. Prefer total assets (AUM); fall back to market cap.
        try:
            info = ticker.get_info() or {}
        except Exception:
            info = {}
        total_assets = finite(info.get("totalAssets"))
        market_cap = finite(info.get("marketCap"))
        value = total_assets if np.isfinite(total_assets) and total_assets > 0 else market_cap
        basis = "total_assets" if np.isfinite(total_assets) and total_assets > 0 else "market_cap"
        if np.isfinite(value) and value > 0:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return {
                "basis": basis,
                "value": float(value),
                "currency": stock.currency,
                "fetched_at": now,
                "sector": str(info.get("category") or "ETF").strip() or "ETF",
                "meta_fetched_at": now,
                "display_size_native": float(value),
                "display_size_basis": basis,
            }
        return None
    try:
        shares = finite(ticker.fast_info["shares"])
    except Exception:
        shares = np.nan
    if np.isfinite(shares) and shares > 0:
        return {
            "basis": "shares",
            "value": float(shares),
            "currency": stock.currency,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    try:
        market_cap = finite(ticker.fast_info["market_cap"])
    except Exception:
        market_cap = np.nan
    if not np.isfinite(market_cap) or market_cap <= 0:
        try:
            info = ticker.get_info() or {}
            market_cap = finite(info.get("marketCap"))
        except Exception:
            market_cap = np.nan
    if np.isfinite(market_cap) and market_cap > 0:
        return {
            "basis": "market_cap",
            "value": float(market_cap),
            "currency": stock.currency,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    return None


def resolve_market_size(stock: Stock, close: float, thresholds: dict, size_cache: dict):
    old = size_cache.get(stock.ticker) if isinstance(size_cache.get(stock.ticker), dict) else {}
    entry = old

    # Migrate the previous ETF display-only cache in place. Older v11 snapshots
    # may already have AUM/market-cap metadata even though they did not store it
    # in the generic size basis fields.
    if stock.category in ETF_CATEGORIES:
        cached_display = finite(old.get("display_size_native"))
        cached_basis = str(old.get("display_size_basis") or "")
        if (
            np.isfinite(cached_display) and cached_display > 0
            and cached_basis in {"market_cap", "total_assets"}
            and _age_days(old.get("meta_fetched_at")) <= DISPLAY_META_CACHE_DAYS
        ):
            entry = dict(old)
            entry["basis"] = cached_basis
            entry["value"] = float(cached_display)
            entry["fetched_at"] = old.get("meta_fetched_at")
            size_cache[stock.ticker] = entry

    if not entry or _age_days(entry.get("fetched_at")) > STOCK_SHARES_CACHE_DAYS:
        fetched = None
        attempts = 1 if stock.category in ETF_CATEGORIES else MARKET_SIZE_RETRY_ATTEMPTS
        for attempt in range(1, attempts + 1):
            try:
                fetched = _fetch_stock_size_basis(stock)
                if fetched:
                    # Preserve display metadata fields that may have a different TTL.
                    for k in ("sector", "meta_fetched_at", "display_size_native", "display_size_basis"):
                        if k in old and k not in fetched:
                            fetched[k] = old[k]
                    size_cache[stock.ticker] = fetched
                    entry = fetched
                    break
                if attempt < attempts:
                    time.sleep(min(8.0, 1.2 * (2 ** (attempt - 1))) + random.uniform(0.05, 0.25))
            except Exception as exc:
                if attempt == attempts:
                    print(f"[{stock.category}] size lookup failed {stock.ticker}: {type(exc).__name__}: {exc}")
                if attempt < attempts:
                    time.sleep(min(8.0, 1.2 * (2 ** (attempt - 1))) + random.uniform(0.05, 0.25))

    if not isinstance(entry, dict):
        return None
    basis = str(entry.get("basis") or "")
    value = finite(entry.get("value"))
    if not np.isfinite(value) or value <= 0:
        return None

    native_size = value * close if basis == "shares" else value if basis in {"market_cap", "total_assets"} else np.nan
    if not np.isfinite(native_size) or native_size <= 0:
        return None

    if stock.currency == "KRW":
        size_krw = native_size
    else:
        fx = finite(thresholds.get("usdkrw"))
        if not np.isfinite(fx) or fx <= 0:
            return None
        size_krw = native_size * fx

    # Persist a recent computed size so later QUICK scans can avoid downloading
    # obviously sub-threshold US equities. A generous 75% cutoff is used later
    # so boundary names are always rechecked with fresh prices.
    if isinstance(entry, dict):
        entry["last_close"] = float(close)
        entry["last_size_native"] = float(native_size)
        entry["last_size_krw"] = float(size_krw)
        entry["last_size_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        size_cache[stock.ticker] = entry
    return float(native_size), float(size_krw), basis


def _to_krw(native_value: float, currency: str, thresholds: dict) -> float:
    if not np.isfinite(native_value) or native_value <= 0:
        return np.nan
    if currency == "KRW":
        return float(native_value)
    fx = finite(thresholds.get("usdkrw"))
    return float(native_value * fx) if np.isfinite(fx) and fx > 0 else np.nan


def enrich_display_metadata(stock: Stock, item: dict, thresholds: dict, size_cache: dict) -> None:
    """Populate sector and display market size for top-ranked cards only.

    The metadata is cached for 45 days. ETF total assets are accepted as the
    closest practical size proxy; if Yahoo does not expose it, the UI shows —.
    Failures never invalidate the technical scan.
    """
    entry = size_cache.get(stock.ticker)
    if not isinstance(entry, dict):
        entry = {}
        size_cache[stock.ticker] = entry

    cached_ok = _age_days(entry.get("meta_fetched_at")) <= DISPLAY_META_CACHE_DAYS
    sector = str(entry.get("sector") or "").strip()
    display_native = finite(entry.get("display_size_native"))
    display_basis = str(entry.get("display_size_basis") or "")

    # Equities already have a reliable size from the universe hard filter.
    existing_krw = finite(item.get("market_size_krw"))

    if not cached_ok:
        try:
            info = yf.Ticker(stock.ticker).get_info() or {}
            if stock.category in ETF_CATEGORIES:
                sector = str(info.get("category") or "ETF").strip() or "ETF"
                display_native = finite(info.get("totalAssets"))
                display_basis = "total_assets"
                if not np.isfinite(display_native) or display_native <= 0:
                    display_native = finite(info.get("marketCap"))
                    display_basis = "market_cap"
            else:
                sector = str(info.get("sector") or info.get("industry") or "").strip()
                if not np.isfinite(existing_krw) or existing_krw <= 0:
                    display_native = finite(info.get("marketCap"))
                    display_basis = "market_cap"

            entry["sector"] = sector
            entry["meta_fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if np.isfinite(display_native) and display_native > 0:
                entry["display_size_native"] = float(display_native)
                entry["display_size_basis"] = display_basis
            size_cache[stock.ticker] = entry
        except Exception as exc:
            print(f"[{stock.category}] display metadata unavailable {stock.ticker}: {type(exc).__name__}: {exc}")

    if not sector:
        sector = "ETF" if stock.category in ETF_CATEGORIES else "—"
    item["sector"] = sector

    if np.isfinite(existing_krw) and existing_krw > 0:
        item["market_size_krw"] = clean(existing_krw, 0)
        return

    display_native = finite(entry.get("display_size_native"), display_native)
    display_krw = _to_krw(display_native, stock.currency, thresholds)
    item["market_size_krw"] = clean(display_krw, 0)



# -----------------------------------------------------------------------------
# Scan acceleration / stale-symbol quarantine
# -----------------------------------------------------------------------------

def _category_data_dir(category: str) -> Path:
    return DATA_DIR / CATEGORY_DIR[category]


def _yahoo_quarantine_path(category: str) -> Path:
    return _category_data_dir(category) / "yahoo-unavailable.json"


def _load_yahoo_quarantine(category: str) -> dict[str, dict]:
    path = _yahoo_quarantine_path(category)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    now = datetime.now(timezone.utc)
    live: dict[str, dict] = {}
    for ticker, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        try:
            until = datetime.fromisoformat(str(entry.get("skip_until") or "").replace("Z", "+00:00"))
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if until > now:
            live[str(ticker)] = entry
    return live


def _save_yahoo_quarantine(category: str, entries: dict[str, dict]) -> None:
    _atomic_write_text(
        _yahoo_quarantine_path(category),
        json.dumps(entries, ensure_ascii=False, separators=(",", ":")),
    )



def _prefilter_us_equities_from_yahoo_screener(
    universe: list[Stock],
    thresholds: dict,
    size_cache: dict,
) -> tuple[list[Stock], dict]:
    """Best-effort bulk prefilter for US equities using Yahoo's screener API.

    We query at 80% of the app's hard 10T-KRW threshold, then intersect the
    result with the Nasdaq Trader common-equity universe. The 20% buffer avoids
    boundary omissions from FX/intraday market-cap differences. If yfinance's
    screener is unavailable, pagination is incomplete, or the result is
    implausibly small, the caller receives the original universe unchanged.
    """
    fx = finite(thresholds.get("usdkrw"))
    if not np.isfinite(fx) or fx <= 0:
        return universe, {"used": False, "reason": "usdkrw_unavailable"}
    screen_fn = getattr(yf, "screen", None)
    query_cls = getattr(yf, "EquityQuery", None)
    if not callable(screen_fn) or query_cls is None:
        return universe, {"used": False, "reason": "yfinance_screen_api_unavailable"}

    min_native = (MIN_MARKET_SIZE_KRW / fx) * 0.80
    page_size = 250
    max_pages = 20
    quotes: list[dict] = []
    total = None
    seen_symbols: set[str] = set()
    try:
        query = query_cls("gt", ["intradaymarketcap", float(min_native)])
        for page in range(max_pages):
            offset = page * page_size
            response = screen_fn(
                query,
                offset=offset,
                size=page_size,
                sortField="intradaymarketcap",
                sortAsc=False,
            )
            if not isinstance(response, dict):
                raise RuntimeError("Yahoo screener returned non-dict payload")
            page_quotes = response.get("quotes") or []
            if not isinstance(page_quotes, list):
                raise RuntimeError("Yahoo screener quotes missing")
            if total is None:
                try:
                    total = int(response.get("total"))
                except Exception:
                    total = None
            page_symbols = {
                str(q.get("symbol") or "").strip().upper().replace(".", "-")
                for q in page_quotes if isinstance(q, dict) and q.get("symbol")
            }
            new_symbols = page_symbols - seen_symbols
            if page > 0 and page_quotes and not new_symbols:
                raise RuntimeError("Yahoo screener pagination made no progress")
            seen_symbols.update(page_symbols)
            quotes.extend(q for q in page_quotes if isinstance(q, dict))
            if len(page_quotes) < page_size:
                break
            if total is not None and len(seen_symbols) >= total:
                break
        if total is not None and total > len(seen_symbols) and len(seen_symbols) >= page_size * max_pages:
            raise RuntimeError(f"Yahoo screener pagination capped before total={total}")

        candidates: set[str] = set()
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for quote in quotes:
            symbol = str(quote.get("symbol") or "").strip().upper().replace(".", "-")
            if not symbol:
                continue
            candidates.add(symbol)
            market_cap = finite(
                quote.get("marketCap")
                if quote.get("marketCap") is not None
                else quote.get("intradaymarketcap")
            )
            if np.isfinite(market_cap) and market_cap > 0:
                old = size_cache.get(symbol) if isinstance(size_cache.get(symbol), dict) else {}
                entry = dict(old)
                entry.update({
                    "basis": "market_cap",
                    "value": float(market_cap),
                    "currency": "USD",
                    "fetched_at": fetched_at,
                    "source": "yahoo_bulk_screener",
                })
                size_cache[symbol] = entry

        selected = [s for s in universe if s.ticker.upper() in candidates]
        # A real 10T-KRW candidate universe should comfortably exceed this.
        if len(selected) < 50:
            raise RuntimeError(f"Yahoo screener intersection implausibly small: {len(selected)}")
        print(
            f"[US] Yahoo bulk market-cap prefilter: {len(universe):,} -> {len(selected):,} "
            f"(query floor ~KRW {MIN_MARKET_SIZE_KRW*0.80/1e12:.1f}T, exact 10T filter still applied later)"
        )
        return selected, {
            "used": True,
            "input": len(universe),
            "selected": len(selected),
            "query_floor_krw": MIN_MARKET_SIZE_KRW * 0.80,
            "quotes": len(quotes),
            "reported_total": total,
        }
    except Exception as exc:
        print(f"[US] Yahoo bulk market-cap prefilter unavailable; full-universe fallback: {type(exc).__name__}: {exc}")
        return universe, {"used": False, "reason": f"{type(exc).__name__}:{exc}"}


def _cached_quick_size_prefilter(
    category: str,
    universe: list[Stock],
    size_cache: dict,
    scan_mode: str,
) -> tuple[list[Stock], int]:
    """Skip only clearly-small cached US equities on QUICK scans.

    A stock is excluded only when its computed KRW market size is <=75% of the
    10T hard threshold and the value was refreshed within seven days. This gives
    a very large safety margin for fast movers and never affects FULL scans.
    """
    if scan_mode != "QUICK" or category != "US":
        return universe, 0
    cutoff = MIN_MARKET_SIZE_KRW * 0.75
    kept: list[Stock] = []
    skipped = 0
    for stock in universe:
        entry = size_cache.get(stock.ticker) if isinstance(size_cache.get(stock.ticker), dict) else {}
        value = finite(entry.get("last_size_krw"))
        age = _age_days(entry.get("last_size_at"))
        if np.isfinite(value) and value > 0 and age <= 7 and value < cutoff:
            skipped += 1
            continue
        kept.append(stock)
    return kept, skipped


def prepare_frame(raw_frame: pd.DataFrame, category: str, scan_mode: str) -> pd.DataFrame:
    frame = _numeric_ohlc(raw_frame)
    return completed_daily(frame, category, include_active_day=(scan_mode == "QUICK"))


def analyze_prepared(stock: Stock, frame: pd.DataFrame, thresholds: dict, size_cache: dict):
    if frame.empty:
        return None, "no_price"
    if len(frame) < MIN_TRADING_DAYS:
        return None, "history_lt_604"

    pos = len(frame) - 1
    close = finite(frame["Close"].iloc[pos])
    if not np.isfinite(close) or close < thresholds["min_price"]:
        return None, "price_lt_threshold"

    size_info = resolve_market_size(stock, close, thresholds, size_cache)
    if stock.category in ETF_CATEGORIES:
        if size_info is None:
            market_size_native, market_size_krw, market_size_basis = np.nan, np.nan, "unavailable"
        else:
            market_size_native, market_size_krw, market_size_basis = size_info
    else:
        if size_info is None:
            return None, "market_size_unavailable"
        market_size_native, market_size_krw, market_size_basis = size_info
        if market_size_krw < MIN_MARKET_SIZE_KRW:
            return None, "market_size_lt_10t"

    # The opinion engine intentionally uses one indicator only: Supertrend(10, 2).
    st_data = analyze_supertrend(frame, period=10, multiplier=2.0, market=stock.category)
    st_research = st_data.pop("_research", {})
    prev_close = finite(frame["Close"].iloc[-2]) if len(frame) >= 2 else np.nan
    day_change = (close / prev_close - 1.0) * 100.0 if np.isfinite(prev_close) and prev_close > 0 else np.nan

    item = {
        "ticker": stock.ticker,
        "symbol": stock.symbol,
        "name": stock.name,
        "category": stock.category,
        "exchange": stock.exchange,
        "currency": stock.currency,
        "date": pd.Timestamp(frame.index[-1]).date().isoformat(),
        "close": clean(close),
        "day_change_pct": clean(day_change, 2),
        "rank": None,
        "supertrend": st_data,
        "_supertrend_research": st_research,
        "opinion": st_data.get("opinion", "Hold"),
        "opinion_code": st_data.get("opinion_code", "HOLD"),
        "opinion_label": st_data.get("opinion_label", "Hold"),
        "hold_reason": st_data.get("hold_reason"),
        "r_pct": clean(st_data.get("r_pct"), 4),
        "p0": clean(st_data.get("p0")),
        "p1": clean(st_data.get("p1")),
        "stop_pct": clean(st_data.get("stop_pct"), 4),
        "atr_pct": clean(st_data.get("atr_pct"), 4),
        "g_atr": clean(st_data.get("g_atr"), 4),
        "d_atr": clean(st_data.get("d_atr"), 4),
        "bars_since_flip": st_data.get("bars_since_flip"),
        "bars_since_gate": st_data.get("bars_since_gate"),
        "r_at_gate": clean(st_data.get("r_at_gate"), 4),
        "new_sell": bool(st_data.get("new_sell", False)),
        "rank_level": int(st_data.get("rank_level", OPINION_ORDER["HOLD"])),
        "sector": "ETF" if stock.category in ETF_CATEGORIES else "—",
        "market_size_krw": clean(market_size_krw, 0),
        "market_cap": clean(market_size_krw, 0),
        "market_size_basis": market_size_basis,
        "metrics": {
            "market_size_native": clean(market_size_native, 0),
            "market_size_krw": clean(market_size_krw, 0),
            "market_size_basis": market_size_basis,
        },
    }
    return item, "passed"


def frame_for(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        for level in range(raw.columns.nlevels):
            values = set(map(str, raw.columns.get_level_values(level)))
            if ticker in values:
                try:
                    return raw.xs(ticker, axis=1, level=level).copy()
                except Exception:
                    pass
        return pd.DataFrame()
    return raw.copy()


def download_batch(tickers: list[str], scan_mode: str = "FULL", timeout=40) -> pd.DataFrame:
    end = datetime.now(timezone.utc).date() + timedelta(days=1)
    history_days = FULL_HISTORY_CALENDAR_DAYS if scan_mode == "FULL" else QUICK_HISTORY_CALENDAR_DAYS
    start = end - timedelta(days=history_days)
    return yf.download(
        tickers=tickers,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        actions=False,
        progress=False,
        threads=min(DOWNLOAD_THREADS, max(1, len(tickers))),
        timeout=timeout,
        multi_level_index=True,
    )


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _load_restrictions(category: str, universe: list[Stock]):
    if category == "KR":
        return fetch_kr_restricted_symbols(universe)
    if category == "KR_ETF":
        return set(), {"source": "USER_ETF_WHITELIST", "restricted_count": 0}
    halted, meta = fetch_us_halted_symbols()
    return halted, meta


# -----------------------------------------------------------------------------
# Site payload / bundles
# -----------------------------------------------------------------------------

def _detail_filename(item: dict) -> str:
    symbol = re.sub(r"[^A-Za-z0-9._-]+", "_", str(item.get("symbol") or "stock")).strip("._-")
    symbol = (symbol or "stock")[:36]
    digest = hashlib.sha1(str(item["ticker"]).encode("utf-8")).hexdigest()[:12]
    return f"{symbol}-{digest}.json"


def _summary_item(item: dict, detail_path: str) -> dict:
    st = dict(item.get("supertrend") or {})
    st.pop("chart", None)
    bt = dict(st.get("backtest") or {})
    bt.pop("recent_trades", None)
    st["backtest"] = bt
    return {
        "ticker": item["ticker"],
        "symbol": item["symbol"],
        "name": item["name"],
        "category": item["category"],
        "exchange": item["exchange"],
        "currency": item["currency"],
        "date": item["date"],
        "close": item["close"],
        "day_change_pct": item["day_change_pct"],
        "rank": item["rank"],
        "opinion": item.get("opinion", "Hold"),
        "opinion_code": item.get("opinion_code", "HOLD"),
        "opinion_label": item.get("opinion_label", "Hold"),
        "hold_reason": item.get("hold_reason"),
        "r_pct": item.get("r_pct"),
        "p0": item.get("p0"),
        "p1": item.get("p1"),
        "stop_pct": item.get("stop_pct"),
        "atr_pct": item.get("atr_pct"),
        "g_atr": item.get("g_atr"),
        "d_atr": item.get("d_atr"),
        "bars_since_flip": item.get("bars_since_flip"),
        "bars_since_gate": item.get("bars_since_gate"),
        "r_at_gate": item.get("r_at_gate"),
        "new_sell": bool(item.get("new_sell", False)),
        "rank_level": item.get("rank_level", OPINION_ORDER["HOLD"]),
        "supertrend": st,
        "sector": item.get("sector") or "—",
        "market_size_krw": item.get("market_size_krw"),
        "market_cap": item.get("market_cap", item.get("market_size_krw")),
        "market_size_basis": item.get("market_size_basis"),
        "detail_path": detail_path,
    }


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{random.randint(1000, 9999)}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp-{os.getpid()}-{random.randint(1000, 9999)}")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def _aggregate_trade_stats(trades: list[dict]) -> dict:
    returns = np.array([finite(t.get("return_pct")) for t in trades], dtype=float)
    returns = returns[np.isfinite(returns)]
    holds = np.array([finite(t.get("holding_bars")) for t in trades], dtype=float)
    holds = holds[np.isfinite(holds)]
    if not len(returns):
        return {
            "trades": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "median_return_pct": None,
            "avg_gain_pct": None,
            "avg_loss_pct": None,
            "payoff_ratio": None,
            "avg_holding_bars": None,
            "max_gain_pct": None,
            "max_loss_pct": None,
        }
    gains = returns[returns > 0]
    losses = returns[returns < 0]
    avg_gain = float(np.mean(gains)) if len(gains) else None
    avg_loss = float(np.mean(losses)) if len(losses) else None
    payoff = avg_gain / abs(avg_loss) if avg_gain is not None and avg_loss not in (None, 0.0) else None
    return {
        "trades": int(len(returns)),
        "win_rate_pct": clean(np.mean(returns > 0) * 100.0, 2),
        "avg_return_pct": clean(np.mean(returns), 3),
        "median_return_pct": clean(np.median(returns), 3),
        "avg_gain_pct": clean(avg_gain, 3),
        "avg_loss_pct": clean(avg_loss, 3),
        "payoff_ratio": clean(payoff, 3),
        "avg_holding_bars": clean(np.mean(holds), 2) if len(holds) else None,
        "max_gain_pct": clean(np.max(returns), 3),
        "max_loss_pct": clean(np.min(returns), 3),
    }


def _distribution_stats(values) -> dict:
    arr = np.array([finite(x) for x in values], dtype=float)
    arr = arr[np.isfinite(arr)]
    if not len(arr):
        return {"count": 0, "mean": None, "median": None, "p25": None, "p75": None}
    return {
        "count": int(len(arr)),
        "mean": clean(np.mean(arr), 4),
        "median": clean(np.median(arr), 4),
        "p25": clean(np.percentile(arr, 25), 4),
        "p75": clean(np.percentile(arr, 75), 4),
    }


def _grade_validation_table(samples: list[dict]) -> list[dict]:
    order = ["BUY_S", "BUY_A", "BUY_B", "BUY_C", "HOLD_OVEREXTENDED"]
    out = []
    for grade in order:
        rows = [r for r in samples if r.get("grade") == grade]
        legs = {(str(r.get("ticker")), int(r.get("leg_id"))) for r in rows if r.get("leg_id") is not None}
        row = {"grade": grade, "samples": len(rows), "legs": len(legs)}
        for key in ("fwd_5d_pct", "fwd_20d_pct", "fwd_60d_pct", "to_sell_pct"):
            vals = [finite(r.get(key)) for r in rows]
            vals = [v for v in vals if np.isfinite(v)]
            row[f"{key}_n"] = len(vals)
            row[f"{key}_avg"] = clean(np.mean(vals), 3) if vals else None
            row[f"{key}_median"] = clean(np.median(vals), 3) if vals else None
        out.append(row)
    return out


def _gate_histogram(gate_events: list[dict]) -> list[dict]:
    # Fine enough to diagnose whether S (<2%) is structurally rare while still
    # keeping the JSON/report compact.
    edges = [0, 1, 2, 3, 5, 10, 20, 40, float("inf")]
    labels = ["0~1", "1~2", "2~3", "3~5", "5~10", "10~20", "20~40", "40+"]
    counts = [0] * len(labels)
    for event in gate_events:
        v = finite(event.get("r_at_gate"))
        if not np.isfinite(v):
            continue
        for i in range(len(labels)):
            if edges[i] <= v < edges[i + 1]:
                counts[i] += 1
                break
    total = sum(counts)
    return [
        {"bin_pct": labels[i], "count": counts[i], "share_pct": clean(counts[i] / total * 100.0, 2) if total else None}
        for i in range(len(labels))
    ]


def _daily_opinion_distribution(items: list[dict]) -> list[dict]:
    by_date: dict[str, Counter] = {}
    for item in items:
        research = item.get("_supertrend_research") or {}
        for row in research.get("daily_opinions") or []:
            date = str(row.get("date") or "")
            code = str(row.get("opinion_code") or "HOLD")
            if not date:
                continue
            by_date.setdefault(date, Counter())[code] += 1
    rows = []
    for date in sorted(by_date)[-60:]:
        c = by_date[date]
        rows.append({
            "date": date,
            "BUY_S": int(c["BUY_S"]),
            "BUY_A": int(c["BUY_A"]),
            "BUY_B": int(c["BUY_B"]),
            "BUY_C": int(c["BUY_C"]),
            "HOLD": int(c["HOLD"]),
            "SELL": int(c["SELL"]),
            "total": int(sum(c.values())),
        })
    return rows


_BENCHMARK_CACHE: dict[str, dict] = {}


def _index_benchmark(category: str) -> dict:
    group = "KR" if category.startswith("KR") else "US"
    if group in _BENCHMARK_CACHE:
        return _BENCHMARK_CACHE[group]
    ticker = "^KS200" if group == "KR" else "^GSPC"
    label = "KOSPI200" if group == "KR" else "S&P500"
    try:
        end = datetime.now(timezone.utc).date() + timedelta(days=1)
        start = end - timedelta(days=760)
        raw = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            auto_adjust=True,
            actions=False,
            progress=False,
            threads=False,
            timeout=30,
        )
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.xs(ticker, axis=1, level=-1) if ticker in set(map(str, raw.columns.get_level_values(-1))) else raw.droplevel(-1, axis=1)
        frame = _numeric_ohlc(raw)
        if len(frame) >= 2:
            last_ts = pd.Timestamp(frame.index[-1])
            cutoff = last_ts - pd.DateOffset(years=2)
            w = frame.loc[frame.index >= cutoff]
            if len(w) >= 2:
                ret = (finite(w["Close"].iloc[-1]) / finite(w["Close"].iloc[0]) - 1.0) * 100.0
                result = {"ticker": ticker, "label": label, "return_pct": clean(ret, 3), "available": True}
                _BENCHMARK_CACHE[group] = result
                return result
    except Exception as exc:
        print(f"[{category}] benchmark {ticker} unavailable: {type(exc).__name__}: {exc}")
    result = {"ticker": ticker, "label": label, "return_pct": None, "available": False}
    _BENCHMARK_CACHE[group] = result
    return result


def _aggregate_supertrend_backtest(category: str, items: list[dict]) -> dict:
    completed_trades: list[dict] = []
    open_trades: list[dict] = []
    grade_samples: list[dict] = []
    first_grade_samples: list[dict] = []
    gate_events: list[dict] = []
    buy_hold = []

    for item in items:
        ticker = item.get("ticker")
        st = item.get("supertrend") or {}
        bt = st.get("backtest") or {}
        bh = finite(bt.get("buy_hold_return_pct"))
        if np.isfinite(bh):
            buy_hold.append(bh)
        research = item.get("_supertrend_research") or {}
        for t in research.get("trades") or []:
            completed_trades.append({**t, "ticker": ticker})
        for t in research.get("open_trades") or []:
            open_trades.append({**t, "ticker": ticker})
        for row in research.get("grade_samples") or []:
            grade_samples.append({**row, "ticker": ticker})
        for row in research.get("first_grade_samples") or []:
            first_grade_samples.append({**row, "ticker": ticker})
        for row in research.get("gate_events") or []:
            gate_events.append({**row, "ticker": ticker})

    completed_stats = _aggregate_trade_stats(completed_trades)
    including_open_stats = _aggregate_trade_stats(completed_trades + open_trades)
    grade_all = _grade_validation_table(grade_samples)
    grade_first = _grade_validation_table(first_grade_samples)
    atr_by_grade = []
    for grade in ["BUY_S", "BUY_A", "BUY_B", "BUY_C", "HOLD_OVEREXTENDED"]:
        vals = [r.get("atr_pct") for r in grade_samples if r.get("grade") == grade]
        atr_by_grade.append({"grade": grade, **_distribution_stats(vals)})

    # Monotonicity diagnostic for the four buy grades. This is diagnostic only;
    # it never changes the fixed 2/5/10/20 thresholds.
    monotonic = {}
    for metric in ("fwd_5d_pct_avg", "fwd_20d_pct_avg", "fwd_60d_pct_avg", "to_sell_pct_avg"):
        vals = []
        for grade in ["BUY_S", "BUY_A", "BUY_B", "BUY_C"]:
            row = next((x for x in grade_first if x["grade"] == grade), None)
            vals.append(row.get(metric) if row else None)
        comparable = all(v is not None and np.isfinite(finite(v)) for v in vals)
        monotonic[metric] = bool(comparable and vals[0] >= vals[1] >= vals[2] >= vals[3]) if comparable else None

    latest_dist = _daily_opinion_distribution(items)
    s_counts = [row["BUY_S"] for row in latest_dist]
    s_warning = None
    if s_counts:
        if max(s_counts) == 0:
            s_warning = "최근 60거래일 매수S가 0건: S 임계값 표본 부족 가능성"
        elif np.mean(s_counts) > max(5, np.mean([r["total"] for r in latest_dist]) * 0.20):
            s_warning = "최근 60거래일 매수S 비중이 높음: 등급 과다 분류 여부 점검 필요"

    return {
        "model": "SUPER_TREND_10_2_V13_2",
        "window": "last 2 calendar years",
        "execution": "signal at close; trade next bar open; max 1 entry per rising leg",
        "completed": completed_stats,
        "including_open": including_open_stats,
        "open_trades": int(len(open_trades)),
        "open_mark_returns": _distribution_stats([t.get("return_pct") for t in open_trades]),
        "same_stock_buy_hold": _distribution_stats(buy_hold),
        "index_benchmark": _index_benchmark(category),
        "grade_validation_all_post_gate_bars": grade_all,
        "grade_validation_first_grade_per_leg": grade_first,
        "grade_monotonicity_first_per_leg": monotonic,
        "r_at_gate_histogram": _gate_histogram(gate_events),
        "gate_events": int(len(gate_events)),
        "atr_pct_by_grade": atr_by_grade,
        "daily_opinion_distribution_60d": latest_dist,
        "distribution_warning": s_warning,
        "cost_note": "KR sell tax 0.18%, fee 0.015%/side, slippage 0.10%/side; US sell tax 0; FX excluded",
        "bias_note": "Current-universe historical test contains survivorship bias. Interpret 2Y absolute return versus buy-and-hold/index benchmark.",
    }


def _fmt_pct(value) -> str:
    v = finite(value)
    return "—" if not np.isfinite(v) else f"{v:+.2f}%"


def _supertrend_report_markdown(category: str, diag: dict) -> str:
    comp = diag.get("completed") or {}
    inc = diag.get("including_open") or {}
    bench = diag.get("index_benchmark") or {}
    bh = diag.get("same_stock_buy_hold") or {}
    lines = [
        f"# DTC SuperTrend(10,2) 백테스트 — {CATEGORY_LABEL.get(category, category)}",
        "",
        f"생성시각(UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## 메인 백테스트",
        "",
        "- 진입: 상승 레그에서 최초 매수S 신호의 다음 봉 시가",
        "- 청산: 상승→하락 전환 신호의 다음 봉 시가",
        "- 레그당 최대 1회 진입",
        "- 비용: KR 매도세 0.18%, 수수료 편도 0.015%, 슬리피지 편도 0.10%; US 매도세 0; 환율 미반영",
        "",
        "| 구분 | 거래수 | 승률 | 평균 | 중앙값 | 평균이익 | 평균손실 | 손익비 | 평균보유봉 | 최대이익 | 최대손실 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| 완료 거래 | {comp.get('trades',0)} | {_fmt_pct(comp.get('win_rate_pct'))} | {_fmt_pct(comp.get('avg_return_pct'))} | {_fmt_pct(comp.get('median_return_pct'))} | {_fmt_pct(comp.get('avg_gain_pct'))} | {_fmt_pct(comp.get('avg_loss_pct'))} | {comp.get('payoff_ratio') if comp.get('payoff_ratio') is not None else '—'} | {comp.get('avg_holding_bars') if comp.get('avg_holding_bars') is not None else '—'} | {_fmt_pct(comp.get('max_gain_pct'))} | {_fmt_pct(comp.get('max_loss_pct'))} |",
        f"| 미청산 평가 포함 | {inc.get('trades',0)} | {_fmt_pct(inc.get('win_rate_pct'))} | {_fmt_pct(inc.get('avg_return_pct'))} | {_fmt_pct(inc.get('median_return_pct'))} | {_fmt_pct(inc.get('avg_gain_pct'))} | {_fmt_pct(inc.get('avg_loss_pct'))} | {inc.get('payoff_ratio') if inc.get('payoff_ratio') is not None else '—'} | {inc.get('avg_holding_bars') if inc.get('avg_holding_bars') is not None else '—'} | {_fmt_pct(inc.get('max_gain_pct'))} | {_fmt_pct(inc.get('max_loss_pct'))} |",
        "",
        f"미청산 거래: **{diag.get('open_trades',0)}건** · 미청산 평가손익 평균: **{_fmt_pct((diag.get('open_mark_returns') or {}).get('mean'))}** · 동일 종목 2Y Buy&Hold 평균: **{_fmt_pct(bh.get('mean'))}** · {bench.get('label','지수')} 2Y: **{_fmt_pct(bench.get('return_pct'))}**",
        "",
        "## 등급 검증 — 게이트 이후 모든 봉",
        "",
        "| 등급 | 표본 | 레그 | +5D 평균 | +20D 평균 | +60D 평균 | 매도신호까지 평균 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in diag.get("grade_validation_all_post_gate_bars") or []:
        lines.append(f"| {row['grade']} | {row['samples']} | {row['legs']} | {_fmt_pct(row.get('fwd_5d_pct_avg'))} | {_fmt_pct(row.get('fwd_20d_pct_avg'))} | {_fmt_pct(row.get('fwd_60d_pct_avg'))} | {_fmt_pct(row.get('to_sell_pct_avg'))} |")
    lines += [
        "",
        "## 등급 검증 — 레그당 각 등급 최초 발생 봉",
        "",
        "| 등급 | 표본 | 레그 | +5D 평균 | +20D 평균 | +60D 평균 | 매도신호까지 평균 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in diag.get("grade_validation_first_grade_per_leg") or []:
        lines.append(f"| {row['grade']} | {row['samples']} | {row['legs']} | {_fmt_pct(row.get('fwd_5d_pct_avg'))} | {_fmt_pct(row.get('fwd_20d_pct_avg'))} | {_fmt_pct(row.get('fwd_60d_pct_avg'))} | {_fmt_pct(row.get('to_sell_pct_avg'))} |")
    lines += ["", "## r_at_gate 히스토그램", "", "| r 구간(%) | 건수 | 비중 |", "|---|---:|---:|"]
    for row in diag.get("r_at_gate_histogram") or []:
        lines.append(f"| {row['bin_pct']} | {row['count']} | {_fmt_pct(row.get('share_pct'))} |")
    lines += ["", "## 등급별 ATR% 분포", "", "| 등급 | n | 평균 | 중앙값 | P25 | P75 |", "|---|---:|---:|---:|---:|---:|"]
    for row in diag.get("atr_pct_by_grade") or []:
        lines.append(f"| {row['grade']} | {row['count']} | {_fmt_pct(row.get('mean'))} | {_fmt_pct(row.get('median'))} | {_fmt_pct(row.get('p25'))} | {_fmt_pct(row.get('p75'))} |")
    lines += [
        "",
        "## 최근 60거래일 의견 분포",
        "",
        "| 날짜 | 매수S | 매수A | 매수B | 매수C | Hold | 매도 | 합계 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in diag.get("daily_opinion_distribution_60d") or []:
        lines.append(f"| {row['date']} | {row['BUY_S']} | {row['BUY_A']} | {row['BUY_B']} | {row['BUY_C']} | {row['HOLD']} | {row['SELL']} | {row['total']} |")
    lines += [
        "",
        "## 해석 한계",
        "",
        "- 현재 유니버스로 과거를 재구성하므로 상장폐지·편출 종목이 빠지는 **생존 편향**이 존재합니다.",
        "- 최근 2년은 시장 국면 편향이 있으므로 절대수익보다 동일종목 Buy&Hold 및 KOSPI200/S&P500 대비로 해석해야 합니다.",
        "- 등급 진단값(ATR%, g_atr, d_atr 등)은 판정에 사용하지 않습니다.",
    ]
    if diag.get("distribution_warning"):
        lines += ["", f"> 경고: {diag['distribution_warning']}"]
    return "\n".join(lines) + "\n"


def _write_quiz_shard(category: str, items: list[dict], frames: dict[str, pd.DataFrame]) -> int:
    """Publish a lazy-loaded quiz manifest plus per-stock compact OHLCV files."""
    quiz_category_dir = DATA_DIR / "quiz" / CATEGORY_DIR[category]
    stocks_dir = quiz_category_dir / "stocks"
    stocks_dir.mkdir(parents=True, exist_ok=True)
    manifest_items = []
    live_names: set[str] = set()

    for item in items:
        market_size = finite(item.get("market_size_krw"))
        if not np.isfinite(market_size) or market_size < QUIZ_MIN_MARKET_SIZE_KRW:
            continue
        frame = frames.get(item.get("ticker"))
        if frame is None or frame.empty or len(frame) < QUIZ_MIN_POINTS:
            continue
        q = frame.tail(QUIZ_HISTORY_POINTS)
        filename = _detail_filename(item)
        live_names.add(filename)
        detail = {
            "ticker": item.get("ticker"),
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "category": category,
            "currency": item.get("currency"),
            "market_size_krw": clean(market_size, 0),
            "d": [pd.Timestamp(x).date().isoformat() for x in q.index],
            "o": [clean(v, 6) for v in q["Open"]],
            "h": [clean(v, 6) for v in q["High"]],
            "l": [clean(v, 6) for v in q["Low"]],
            "c": [clean(v, 6) for v in q["Close"]],
            "v": [clean(v, 0) for v in q["Volume"]],
        }
        _atomic_write_text(stocks_dir / filename, json.dumps(detail, ensure_ascii=False, separators=(",", ":")))
        manifest_items.append({
            "ticker": item.get("ticker"),
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "category": category,
            "currency": item.get("currency"),
            "market_size_krw": clean(market_size, 0),
            "points": len(q),
            "first_date": pd.Timestamp(q.index[0]).date().isoformat(),
            "last_date": pd.Timestamp(q.index[-1]).date().isoformat(),
            "detail_path": f"data/quiz/{CATEGORY_DIR[category]}/stocks/{filename}",
        })

    manifest = {
        "version": "DTC_QUIZ_V2_LAZY",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "category": category,
        "minimum_market_size_krw": QUIZ_MIN_MARKET_SIZE_KRW,
        "history_points_max": QUIZ_HISTORY_POINTS,
        "items": manifest_items,
    }
    manifest_file = quiz_category_dir / "manifest.json"
    manifest_tmp = quiz_category_dir / f".manifest.build-{os.getpid()}.json"
    manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    bundle_file = quiz_category_dir / "bundle.zip"
    bundle_tmp = quiz_category_dir / f".bundle.build-{os.getpid()}.zip"
    with zipfile.ZipFile(bundle_tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(manifest_tmp, "manifest.json")
        for detail_file in sorted(stocks_dir.glob("*.json")):
            if detail_file.name in live_names:
                zf.write(detail_file, f"stocks/{detail_file.name}")
    os.replace(bundle_tmp, bundle_file)
    os.replace(manifest_tmp, manifest_file)

    for detail_file in stocks_dir.glob("*.json"):
        if detail_file.name not in live_names:
            try:
                detail_file.unlink()
            except OSError:
                pass

    # Remove the short-lived v11.8 development flat shard if present.
    legacy_flat = DATA_DIR / "quiz" / f"{CATEGORY_DIR[category]}.json"
    if legacy_flat.is_file():
        try:
            legacy_flat.unlink()
        except OSError:
            pass

    print(f"[{category}] quiz pool: {len(manifest_items):,} symbols >= 100T KRW (lazy stock files)")
    return len(manifest_items)


def _write_category_site(category: str, payload_meta: dict, items: list[dict], size_cache: dict, scan_mode: str):
    category_dir = DATA_DIR / CATEGORY_DIR[category]
    stocks_dir = category_dir / "stocks"
    stocks_dir.mkdir(parents=True, exist_ok=True)

    summary_items = []
    live_detail_names: set[str] = set()
    for item in items:
        filename = _detail_filename(item)
        live_detail_names.add(filename)
        relative = f"data/{CATEGORY_DIR[category]}/stocks/{filename}"
        summary_items.append(_summary_item(item, relative))
        _atomic_write_text(stocks_dir / filename, json.dumps(item, ensure_ascii=False, separators=(",", ":")))

    summary_payload = {
        **payload_meta,
        "storage_model": "summary_plus_lazy_stock_detail_dtc_v11",
        "detail_count": len(items),
        "items": summary_items,
    }
    summary_file = category_dir / "summary.json"

    sizes_file = category_dir / "sizes.json"
    _atomic_write_text(sizes_file, json.dumps(size_cache, ensure_ascii=False, separators=(",", ":")))

    universe_snapshot = category_dir / "universe.json"
    root_cache = DATA_DIR / UNIVERSE_CACHE_FILE[category]
    if root_cache.is_file():
        _atomic_copy(root_cache, universe_snapshot)

    legacy_backtest_model = category_dir / "backtest_model.json"
    if legacy_backtest_model.is_file():
        try:
            legacy_backtest_model.unlink()
        except OSError:
            pass

    bundle_file = category_dir / "bundle.zip"
    # Build bundle from a temporary summary; publish summary last so clients
    # never receive references to detail files that are not yet complete.
    summary_tmp = category_dir / f".summary.build-{os.getpid()}.json"
    summary_tmp.write_text(json.dumps(summary_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    bundle_tmp = category_dir / f".bundle.build-{os.getpid()}.zip"
    with zipfile.ZipFile(bundle_tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(summary_tmp, "summary.json")
        zf.write(sizes_file, "sizes.json")
        quarantine_file = category_dir / "yahoo-unavailable.json"
        if quarantine_file.is_file():
            zf.write(quarantine_file, "yahoo-unavailable.json")
        if universe_snapshot.is_file():
            zf.write(universe_snapshot, "universe.json")
        report_file = category_dir / "supertrend_backtest_report.md"
        if report_file.is_file():
            zf.write(report_file, "supertrend_backtest_report.md")
        for detail_file in sorted(stocks_dir.glob("*.json")):
            if detail_file.name not in live_detail_names:
                continue
            zf.write(detail_file, f"stocks/{detail_file.name}")
    os.replace(bundle_tmp, bundle_file)
    os.replace(summary_tmp, summary_file)

    # Cleanup stale details only after the new summary is live.
    for detail_file in stocks_dir.glob("*.json"):
        if detail_file.name not in live_detail_names:
            try:
                detail_file.unlink()
            except OSError:
                pass

    return category_dir, len(items)


# -----------------------------------------------------------------------------
# Market scan
# -----------------------------------------------------------------------------

def scan_category(
    category: str,
    usdkrw: float | None = None,
    scan_mode: str = "FULL",
    usdkrw_source: str | None = None,
) -> None:
    universe, universe_source = get_universe(category)
    thresholds = thresholds_for(category, usdkrw)
    restricted, restriction_meta = _load_restrictions(category, universe)
    size_cache = _load_size_cache(category)
    krx_active_symbols: set[str] = set()
    if category == "KR":
        _, krx_active_symbols = _refresh_kr_equity_size_cache_from_krx(size_cache, universe)
    if category == "KR_ETF":
        # Fix KR ETF cards/filtering: Yahoo often omits Korean ETF totalAssets.
        # One Naver snapshot supplies market cap for the full fixed whitelist.
        _refresh_kr_etf_size_cache_from_naver(size_cache)

    print("=" * 76)
    print(f"DTC v13.2 Supertrend | {category} | mode={scan_mode} | universe={len(universe):,} | restricted={len(restricted):,}")
    if category in ETF_CATEGORIES:
        print("ETF universe = fixed user whitelist; equity 10T market-size filter = exempt")
    else:
        print(f"equity market-size filter >= KRW {MIN_MARKET_SIZE_KRW/1e12:.0f}T")
    print("=" * 76)

    results: dict[str, dict] = {}
    frames: dict[str, pd.DataFrame] = {}
    rejection = Counter()
    priced_tickers: set[str] = set()
    missing: list[str] = []
    by_ticker = {s.ticker: s for s in universe}

    scan_universe = [s for s in universe if s.ticker not in restricted and s.symbol not in restricted]
    rejection["restricted_status"] += len(universe) - len(scan_universe)

    # KIND is used for names/status, while the KRX daily market-cap snapshot is
    # the authoritative current tradable-code list. This drops stale/delisted
    # KIND rows (the recurring .KQ/.KS Yahoo 404s) before any Yahoo request.
    if category == "KR" and krx_active_symbols:
        before = len(scan_universe)
        scan_universe = [s for s in scan_universe if s.symbol in krx_active_symbols]
        rejection["not_in_current_krx_snapshot"] += before - len(scan_universe)

        before = len(scan_universe)
        def official_cap_ok(stock: Stock) -> bool:
            entry = size_cache.get(stock.ticker) if isinstance(size_cache.get(stock.ticker), dict) else {}
            if str(entry.get("source") or "") != "krx_MDCSTAT01501":
                return True
            cap = finite(entry.get("value"))
            return not np.isfinite(cap) or cap >= MIN_MARKET_SIZE_KRW
        scan_universe = [s for s in scan_universe if official_cap_ok(s)]
        rejection["krx_market_size_lt_10t_prefilter"] += before - len(scan_universe)

    us_bulk_prefilter_meta = {"used": False, "reason": "not_us"}
    if category == "US":
        scan_universe, us_bulk_prefilter_meta = _prefilter_us_equities_from_yahoo_screener(
            scan_universe, thresholds, size_cache
        )

    # If Yahoo's bulk screener is unavailable, reuse recently-computed sizes for
    # obviously small US equities during QUICK scans. FULL fallback still scans
    # the complete live universe so no candidate is permanently lost.
    if category == "US" and us_bulk_prefilter_meta.get("used"):
        cached_small_skipped = 0
    else:
        scan_universe, cached_small_skipped = _cached_quick_size_prefilter(category, scan_universe, size_cache, scan_mode)
    rejection["cached_small_quick_prefilter"] += cached_small_skipped

    quarantine = _load_yahoo_quarantine(category)
    if quarantine:
        before = len(scan_universe)
        scan_universe = [s for s in scan_universe if s.ticker not in quarantine]
        rejection["yahoo_quarantine"] += before - len(scan_universe)

    print(
        f"[{category}] price-download universe={len(scan_universe):,} "
        f"| KRX-stale={rejection['not_in_current_krx_snapshot']:,} "
        f"| size-prefilter={rejection['krx_market_size_lt_10t_prefilter'] + rejection['cached_small_quick_prefilter']:,} "
        f"| yahoo-quarantine={rejection['yahoo_quarantine']:,}"
    )

    batches = list(chunks(scan_universe, BATCH_SIZE))
    total_batches = len(batches)
    for batch_no, batch in enumerate(batches, 1):
        tickers = [s.ticker for s in batch]
        try:
            raw = download_batch(tickers, scan_mode=scan_mode)
        except Exception as exc:
            print(f"[{category}] batch {batch_no}/{total_batches} failed: {type(exc).__name__}: {exc}")
            missing.extend(tickers)
            time.sleep(1.5)
            continue

        for stock in batch:
            try:
                raw_frame = frame_for(raw, stock.ticker)
                if raw_frame is None or raw_frame.empty:
                    rejection["no_price"] += 1
                    missing.append(stock.ticker)
                    continue
                priced_tickers.add(stock.ticker)
                frame = prepare_frame(raw_frame, stock.category, scan_mode)
                item, reason = analyze_prepared(stock, frame, thresholds, size_cache)
                rejection[reason] += 1
                if item is not None:
                    results[stock.ticker] = item
                    frames[stock.ticker] = frame
            except Exception as exc:
                rejection["analysis_error"] += 1
                print(f"[{category}] {stock.ticker} analyze error: {type(exc).__name__}: {exc}")

        if batch_no % 10 == 0 or batch_no == total_batches:
            print(
                f"[{category}] {batch_no}/{total_batches} batches "
                f"({batch_no/max(1,total_batches)*100:5.1f}%) | priced={len(priced_tickers):,} | eligible={len(results):,}"
            )
        # Avoid a fixed sleep after every successful batch; it dominated wall
        # time on 2k-5k symbol universes. A short pause every four batches keeps
        # request bursts bounded while retaining Yahoo retry protection.
        if batch_no % 4 == 0 and batch_no != total_batches:
            time.sleep(random.uniform(*PRIMARY_BATCH_SLEEP))

    retry = [t for t in dict.fromkeys(missing) if t not in priced_tickers and t in by_ticker]
    final_unavailable: list[str] = []
    if retry:
        print(f"[{category}] retrying {len(retry):,} unavailable symbols")
        remaining = retry
        attempts = 1 if scan_mode == "QUICK" else RETRY_ATTEMPTS
        for attempt in range(1, attempts + 1):
            if not remaining:
                break
            next_remaining = []
            for batch in chunks(remaining, RETRY_BATCH_SIZE):
                try:
                    raw = download_batch(batch, scan_mode=scan_mode, timeout=55)
                except Exception:
                    next_remaining.extend(batch)
                    continue
                for ticker in batch:
                    stock = by_ticker[ticker]
                    raw_frame = frame_for(raw, ticker)
                    if raw_frame is None or raw_frame.empty:
                        next_remaining.append(ticker)
                        continue
                    priced_tickers.add(ticker)
                    frame = prepare_frame(raw_frame, stock.category, scan_mode)
                    item, reason = analyze_prepared(stock, frame, thresholds, size_cache)
                    rejection[reason] += 1
                    if item is not None:
                        results[ticker] = item
                        frames[ticker] = frame
                time.sleep(random.uniform(*RETRY_BATCH_SLEEP))

            previous = set(remaining)
            remaining = list(dict.fromkeys(next_remaining))
            if remaining and set(remaining) == previous:
                print(f"[{category}] retry made no progress ({len(remaining):,}); stop repeated retries")
                break
            if remaining and attempt < attempts:
                time.sleep(min(30.0, 5.0 * (2 ** (attempt - 1))))
        if remaining:
            final_unavailable = list(remaining)
            print(f"[{category}] final unavailable symbols={len(remaining):,}")

    expected_price_count = len(scan_universe)
    coverage = len(priced_tickers) / max(1, expected_price_count)
    required_coverage = MIN_COVERAGE[category]
    min_absolute = min(100, max(1, expected_price_count))
    if len(priced_tickers) < min_absolute or coverage < required_coverage:
        raise RuntimeError(
            f"{category} price coverage too low: {len(priced_tickers)}/{expected_price_count} "
            f"({coverage:.1%}), required>={required_coverage:.0%}. Existing site data was not overwritten."
        )

    # Healthy FULL scans may quarantine only a tiny tail of repeatedly unavailable
    # symbols. Mass Yahoo failures are never quarantined, preventing a transient
    # outage from silently shrinking the next scan universe.
    for ticker in list(quarantine):
        if ticker in priced_tickers:
            quarantine.pop(ticker, None)
    max_quarantine = max(20, int(max(1, expected_price_count) * YAHOO_QUARANTINE_MAX_RATIO))
    if (
        scan_mode == "FULL"
        and final_unavailable
        and coverage >= YAHOO_QUARANTINE_MIN_HEALTHY_COVERAGE
        and len(final_unavailable) <= max_quarantine
    ):
        now = datetime.now(timezone.utc)
        skip_until = now + timedelta(hours=YAHOO_QUARANTINE_HOURS)
        for ticker in final_unavailable:
            quarantine[ticker] = {
                "failed_at": now.isoformat(timespec="seconds"),
                "skip_until": skip_until.isoformat(timespec="seconds"),
                "reason": "no_daily_price_after_full_retries",
            }
        print(f"[{category}] Yahoo quarantine added={len(final_unavailable):,} until {skip_until.isoformat(timespec='minutes')}")
    _save_yahoo_quarantine(category, quarantine)

    if category not in ETF_CATEGORIES:
        size_attempted = rejection["market_size_lt_10t"] + rejection["market_size_unavailable"] + len(results)
        size_success = rejection["market_size_lt_10t"] + len(results)
        size_coverage = size_success / max(1, size_attempted)
        if size_attempted >= 10 and size_coverage < MARKET_SIZE_MIN_LOOKUP_COVERAGE:
            raise RuntimeError(
                f"{category} market-size lookup coverage too low: {size_success}/{size_attempted} "
                f"({size_coverage:.1%}), required>={MARKET_SIZE_MIN_LOOKUP_COVERAGE:.0%}."
            )
    else:
        size_coverage = np.nan

    # Supertrend strategy ranking: Buy S -> Buy A -> Buy B -> Buy C -> Hold -> Sell.
    # Same-level default is market cap/AUM descending. SELL is the one documented
    # exception: recent (<5 bars) UP->DOWN flips are placed first inside SELL.
    unsorted_items = list(results.values())
    backtest_refreshed = True
    backtest_diagnostics = _aggregate_supertrend_backtest(category, unsorted_items)

    def _rank_key(item: dict):
        level = int(item.get("rank_level", OPINION_ORDER["HOLD"]))
        cap = finite(item.get("market_size_krw"), -1.0)
        sell_recent_key = 0 if (item.get("opinion_code") == "SELL" and item.get("new_sell")) else 1
        return (level, sell_recent_key if level == OPINION_ORDER["SELL"] else 0, -cap, item.get("symbol", ""))

    items = sorted(unsorted_items, key=_rank_key)
    for rank, item in enumerate(items, 1):
        item["rank"] = rank

    top_items = items[:DISPLAY_META_TOP_N]

    # Sector enrichment remains limited to top cards. ETF size itself is already
    # collected for the full universe during analyze_prepared so market-cap/AUM
    # filtering works beyond TOP20.
    print(f"[{category}] display metadata enrichment: top {len(top_items):,}")
    for idx, item in enumerate(top_items, 1):
        stock = by_ticker.get(item["ticker"])
        if stock is None:
            continue
        enrich_display_metadata(stock, item, thresholds, size_cache)
        # Keep detail metrics coherent with the summary display value.
        item["metrics"]["market_size_krw"] = item.get("market_size_krw")
        if idx % 20 == 0 or idx == len(top_items):
            print(f"[{category}] metadata {idx}/{len(top_items)}")
        # Cache normally makes this zero-cost after the first successful fetch.
        if _age_days((size_cache.get(item["ticker"]) or {}).get("meta_fetched_at")) < 0.01:
            time.sleep(random.uniform(0.12, 0.24))

    quiz_manifest = DATA_DIR / "quiz" / CATEGORY_DIR[category] / "manifest.json"
    if scan_mode == "FULL" or not quiz_manifest.is_file():
        quiz_count = _write_quiz_shard(category, items, frames)
    else:
        try:
            quiz_payload = json.loads(quiz_manifest.read_text(encoding="utf-8"))
            quiz_count = len(quiz_payload.get("items") or [])
        except Exception:
            quiz_count = 0
        print(f"[{category}] QUICK: reusing FULL quiz shard ({quiz_count:,} symbols)")

    market_date = max((x["date"] for x in items if x.get("date")), default=None)
    payload_meta = {
        "app": "Dongtan Trading Center",
        "strategy": "SUPER_TREND_10_2_OPINION",
        "category": category,
        "category_label": CATEGORY_LABEL[category],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scan_mode": scan_mode,
        "data_status": "intraday_live" if scan_mode == "QUICK" else "close_confirmed",
        "backtest_refreshed": backtest_refreshed,
        "market_date": market_date,
        "universe_source": universe_source,
        "restriction_snapshot": restriction_meta,
        "universe_count": len(universe),
        "price_download_universe_count": expected_price_count,
        "krx_stale_prefilter_count": rejection["not_in_current_krx_snapshot"],
        "market_size_prefilter_count": rejection["krx_market_size_lt_10t_prefilter"] + rejection["cached_small_quick_prefilter"],
        "us_bulk_marketcap_prefilter": us_bulk_prefilter_meta,
        "yahoo_quarantine_count": rejection["yahoo_quarantine"],
        "priced_count": len(priced_tickers),
        "coverage_pct": round(coverage * 100, 1),
        "passed_count": len(items),
        "quiz_pool_count_ge_100t": quiz_count,
        "market_size_min_krw": None if category in ETF_CATEGORIES else MIN_MARKET_SIZE_KRW,
        "market_size_filter": "exempt" if category in ETF_CATEGORIES else "krw_10t_min",
        "market_size_lookup_coverage_pct": round(size_coverage * 100, 1) if np.isfinite(size_coverage) else None,
        "usdkrw_source": usdkrw_source if category.startswith("US") else None,
        "thresholds": thresholds,
        "filter_counts": dict(sorted(rejection.items())),
        "strategy_model": {
            "name": "Supertrend",
            "period": 10,
            "multiplier": 2.0,
            "opinion_order": ["BUY_S", "BUY_A", "BUY_B", "BUY_C", "HOLD", "SELL"],
            "p0_definition": "ST[flip_idx-1] (last DOWN bar upper-band ST), never ST[flip_idx]",
            "p1_definition": "current ST",
            "atr": "Wilder RMA(10)",
            "warmup_discard_bars": 100,
            "buy_condition": "current Supertrend is UP and P1 >= P0",
            "grades": {
                "BUY_S": "0% <= r < 2%",
                "BUY_A": "2% <= r < 5%",
                "BUY_B": "5% <= r < 10%",
                "BUY_C": "10% <= r < 20%",
            },
            "sell_condition": "current Supertrend direction is DOWN",
            "otherwise": "HOLD",
            "ranking": "opinion level first, then market size descending; recent SELL flips first inside SELL",
            "chart": "126 sessions adjusted real OHLC + Supertrend(10,2), P0/flip/gate markers",
            "chart_colors": "bullish candle/red, bearish candle/blue, ST up/red, ST down/blue",
            "backtest": backtest_diagnostics,
            "backtest_report": f"data/{CATEGORY_DIR[category]}/supertrend_backtest_report.md",
        },
    }

    category_out = DATA_DIR / CATEGORY_DIR[category]
    _atomic_write_text(category_out / "supertrend_backtest_report.md", _supertrend_report_markdown(category, backtest_diagnostics))
    # Research samples are used only to build the aggregate report; never publish
    # the large internal arrays in per-stock JSON.
    for item in items:
        item.pop("_supertrend_research", None)

    out_dir, detail_count = _write_category_site(category, payload_meta, items, size_cache, scan_mode)
    bundle_mb = (out_dir / "bundle.zip").stat().st_size / (1024 * 1024)
    summary_kb = (out_dir / "summary.json").stat().st_size / 1024
    print(
        f"[{category}] wrote {out_dir} | eligible={detail_count:,} | coverage={coverage:.1%} | "
        f"summary={summary_kb:.1f}KB | bundle={bundle_mb:.1f}MB"
    )


def main():
    parser = argparse.ArgumentParser(description="Dongtan Trading Center technical screener")
    parser.add_argument(
        "--market",
        choices=["KR", "KR_ETF", "KR_GROUP", "US", "US_ETF", "US_GROUP", "ALL"],
        default="ALL",
    )
    parser.add_argument(
        "--scan-mode",
        choices=["FULL", "QUICK"],
        default="FULL",
        help="FULL refreshes market/quiz data; QUICK refreshes current Supertrend opinions and reuses the latest FULL quiz shard.",
    )
    args = parser.parse_args()

    markets = {
        "KR": ["KR"],
        "KR_ETF": ["KR_ETF"],
        "KR_GROUP": ["KR", "KR_ETF"],
        "US": ["US"],
        "US_ETF": ["US_ETF"],
        "US_GROUP": ["US", "US_ETF"],
        "ALL": ["KR", "KR_ETF", "US", "US_ETF"],
    }[args.market]

    usdkrw = None
    usdkrw_source = None
    if any(x.startswith("US") for x in markets):
        usdkrw, usdkrw_source = fetch_usdkrw()
        if args.scan_mode == "FULL" and usdkrw_source == "fallback_1400":
            raise SystemExit("FULL scan aborted: live/cached USDKRW unavailable; refusing silent 1400 fallback")
    failures = []
    for category in markets:
        try:
            scan_category(
                category,
                usdkrw=usdkrw,
                scan_mode=args.scan_mode,
                usdkrw_source=usdkrw_source,
            )
        except Exception as exc:
            failures.append((category, exc))
            print(f"ERROR {category}: {type(exc).__name__}: {exc}")
            traceback.print_exc()

    if failures:
        names = ", ".join(category for category, _ in failures)
        raise SystemExit(f"One or more categories failed: {names}")


if __name__ == "__main__":
    main()
