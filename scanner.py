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

MORNING_INVEST_COMPONENT_VERSION = "14.4"

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from universe import Stock, fetch_kr_restricted_symbols, fetch_us_halted_symbols, get_universe
from supertrend_strategy import analyze as analyze_supertrend, OPINION_ORDER
from market_data import download_market_frames, exact_source_preflight, market_data_source_for

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FX_CACHE_FILE = DATA_DIR / "fx_usdkrw.json"
TOSS_SYMBOL_CACHE_FILE = DATA_DIR / "toss_symbol_cache.json"
TOSS_SEARCH_API = "https://wts-info-api.tossinvest.com/api/v1/search-all/wts-auto-complete"
TOSS_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": "https://www.tossinvest.com",
    "Referer": "https://www.tossinvest.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
}

# -----------------------------------------------------------------------------
# Dongtan Trading Center (DTC) scanner v14.4 · Excel workbook UI + dual timeframe SuperTrend
# -----------------------------------------------------------------------------
# Opinion engine: daily + weekly SuperTrend(14,2) gates. ADX(14,14) is reference-only.
#   CASE1 = ST_D UP and current ST_D >= final DOWN ST_D immediately before latest flip.
#   CASE2 = ST_W UP and current ST_W >= final DOWN ST_W immediately before latest flip.
#   CASE1&2 -> 매수; CASE1 only -> 단기 매수; CASE2 only -> 장기 매수.
#   ST_D/ST_W both DOWN -> 매도; exactly one DOWN -> 매도 고려; otherwise HOLD.
# Ranking: 매수 -> 단기/장기 매수 -> HOLD -> 매도 고려 -> 매도, then market size.
# Backtest: 2Y first 매수(CASE1&2) -> first 매도(both DOWN); completed-cycle peak-return median.
# KR OHLC: Toss WTS c-chart. US OHLC: TradingView public chart websocket.
# Chart: ~6 months source-native OHLC + ST_D solid + developing ST_W dashed.
# -----------------------------------------------------------------------------

FULL_HISTORY_CALENDAR_DAYS = 1120  # retained for Yahoo metadata/FX helpers only
# Both FULL and QUICK need >=604 valid sessions: 2Y backtest (~504) + 100-bar
# post-ATR warm-up discard. 980 calendar days leaves a holiday buffer.
QUICK_HISTORY_CALENDAR_DAYS = 980  # retained for Yahoo metadata/FX helpers only
EXACT_HISTORY_BARS = 720
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
DISPLAY_META_TOP_N = 1000
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
        # Keep Adj Close only as metadata.  TradingView's standard daily ADX and
        # SuperTrend are calculated from the chart's OHLC series, not from a
        # dividend-adjusted OHLC reconstruction.  Do NOT multiply O/H/L/C by
        # Adj Close / Close here.
        out["Adj Close"] = pd.to_numeric(out["Adj Close"], errors="coerce")
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



def _load_toss_symbol_cache() -> dict:
    if not TOSS_SYMBOL_CACHE_FILE.is_file():
        return {"updated_at": None, "symbols": {}}
    try:
        data = json.loads(TOSS_SYMBOL_CACHE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid Toss symbol cache")
        data.setdefault("symbols", {})
        return data
    except Exception:
        return {"updated_at": None, "symbols": {}}


def _save_toss_symbol_cache(data: dict) -> None:
    data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _atomic_write_text(TOSS_SYMBOL_CACHE_FILE, json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _direct_toss_product_code(item: dict) -> str:
    existing = str(item.get("toss_product_code") or "").strip().upper()
    if existing:
        return existing
    ticker = str(item.get("symbol") or item.get("ticker") or "").strip().upper()
    ticker = re.sub(r"\.(KS|KQ)$", "", ticker)
    category = str(item.get("category") or "").upper()
    if category.startswith("KR"):
        digits = "".join(ch for ch in ticker if ch.isdigit())
        return "A" + digits.zfill(6)[-6:] if digits else ""
    return ""


def _toss_hits(payload) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("result", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _resolve_toss_product_code(item: dict, cache: dict, timeout: float = 5.0) -> str:
    """Best-effort Toss WTS product code. Failure never affects the scan."""
    direct = _direct_toss_product_code(item)
    if direct:
        return direct
    ticker = str(item.get("symbol") or item.get("ticker") or "").strip().upper().replace(".", "-")
    if not ticker:
        return ""
    symbols = cache.setdefault("symbols", {})
    cached = symbols.get(ticker)
    if isinstance(cached, dict) and cached.get("product_code"):
        return str(cached["product_code"]).strip().upper()
    name = str(item.get("name") or "").strip()
    try:
        response = requests.post(
            TOSS_SEARCH_API,
            json={"query": ticker},
            headers=TOSS_BROWSER_HEADERS,
            timeout=timeout,
        )
        if response.status_code in (403, 429):
            return ""
        response.raise_for_status()
        hits = _toss_hits(response.json())
        chosen = None
        for hit in hits:
            if str(hit.get("symbol") or "").strip().upper() == ticker and hit.get("stockCode"):
                chosen = hit
                break
        if chosen is None and name:
            name_cf = name.casefold()
            for hit in hits:
                fields = (hit.get("companyName"), hit.get("keyword"), hit.get("stockName"))
                if any(str(v or "").strip().casefold() == name_cf for v in fields) and hit.get("stockCode"):
                    chosen = hit
                    break
        usable = [h for h in hits if h.get("stockCode")]
        if chosen is None and len(usable) == 1:
            chosen = usable[0]
        if not chosen:
            return ""
        code = str(chosen.get("stockCode") or "").strip().upper()
        if code:
            symbols[ticker] = {
                "product_code": code,
                "name": str(chosen.get("companyName") or chosen.get("keyword") or name),
                "market": str(chosen.get("market") or item.get("category") or ""),
            }
        return code
    except Exception:
        return ""



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

    # DTC v14.4: exact-source OHLC + daily/weekly SuperTrend(14,2); ADX(14,14) is reference-only.
    st_data = analyze_supertrend(frame, period=14, multiplier=2.0, market=stock.category)
    st_research = st_data.pop("_research", {})
    prev_close = finite(frame["Close"].iloc[-2]) if len(frame) >= 2 else np.nan
    day_change_amount = close - prev_close if np.isfinite(prev_close) else np.nan
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
        "day_change_amount": clean(day_change_amount, 4),
        "day_change_pct": clean(day_change, 2),
        "rank": None,
        "supertrend": st_data,
        "_supertrend_research": st_research,
        "opinion": st_data.get("opinion", "Hold"),
        "opinion_code": st_data.get("opinion_code", "HOLD"),
        "opinion_label": st_data.get("opinion_label", "Hold"),
        "rank_level": int(st_data.get("rank_level", OPINION_ORDER["HOLD"])),
        "adx": clean(st_data.get("adx"), 2),
        "st_d_direction": st_data.get("st_d_direction"),
        "st_w_direction": st_data.get("st_w_direction"),
        "case1": bool(st_data.get("case1", False)),
        "case2": bool(st_data.get("case2", False)),
        "reason": st_data.get("reason") or "",
        "sector": "ETF" if stock.category in ETF_CATEGORIES else "—",
        "market_size_krw": clean(market_size_krw, 0),
        "market_cap": clean(market_size_krw, 0),
        "market_size_basis": market_size_basis,
        "market_data_source": market_data_source_for(stock.category),
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
        auto_adjust=False,
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
    st.pop("chart_events", None)
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
        "day_change_amount": item.get("day_change_amount"),
        "day_change_pct": item["day_change_pct"],
        "rank": item["rank"],
        "opinion": item.get("opinion", "Hold"),
        "opinion_code": item.get("opinion_code", "HOLD"),
        "opinion_label": item.get("opinion_label", "Hold"),
        "rank_level": item.get("rank_level", OPINION_ORDER["HOLD"]),
        "adx": item.get("adx", st.get("adx")),
        "st_d_direction": item.get("st_d_direction", st.get("st_d_direction")),
        "st_w_direction": item.get("st_w_direction", st.get("st_w_direction")),
        "case1": bool(item.get("case1", st.get("case1", False))),
        "case2": bool(item.get("case2", st.get("case2", False))),
        "reason": item.get("reason", st.get("reason") or ""),
        "supertrend": st,
        "sector": item.get("sector") or "—",
        "market_size_krw": item.get("market_size_krw"),
        "market_cap": item.get("market_cap", item.get("market_size_krw")),
        "market_size_basis": item.get("market_size_basis"),
        "toss_product_code": item.get("toss_product_code"),
        "market_data_source": item.get("market_data_source"),
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


def _daily_opinion_distribution(items: list[dict]) -> list[dict]:
    c = Counter(str(item.get("opinion_code") or "HOLD") for item in items)
    if not items:
        return []
    date = max((str(item.get("date") or "") for item in items), default="")
    return [{
        "date": date,
        "BUY": int(c["BUY"]),
        "SHORT_BUY": int(c["SHORT_BUY"]),
        "LONG_BUY": int(c["LONG_BUY"]),
        "HOLD": int(c["HOLD"]),
        "SELL_CONSIDER": int(c["SELL_CONSIDER"]),
        "SELL": int(c["SELL"]),
        "total": int(sum(c.values())),
    }]


def _aggregate_supertrend_backtest(category: str, items: list[dict]) -> dict:
    events: list[dict] = []
    for item in items:
        ticker = item.get("ticker")
        research = item.get("_supertrend_research") or {}
        for event in research.get("events") or []:
            events.append({**event, "ticker": ticker})

    completed = [finite(e.get("max_return_pct")) for e in events if e.get("completed")]
    completed = [float(x) for x in completed if np.isfinite(x)]
    return {
        "model": "DUAL_ST_D_W_GATE_ST14_2_DUALSOURCE_V14_4",
        "window": "last 2 calendar years",
        "median_max_return_pct": clean(float(np.median(completed)), 2) if completed else None,
        "completed_cycles": int(len(completed)),
        "signal_cycles": int(len(events)),
        "entry": "first 매수(CASE1&CASE2) while flat; signal-day close",
        "exit": "first 매도(ST_D DOWN and ST_W DOWN); signal-day close",
        "max_return": "maximum daily High return from entry through exit",
        "headline": "median of completed 매수->매도 cycle maximum returns",
        "current_opinion_distribution": _daily_opinion_distribution(items),
    }


def _supertrend_report_markdown(category: str, diag: dict) -> str:
    med = diag.get("median_max_return_pct")
    med_text = "—" if med is None else f"{float(med):.2f}%"
    lines = [
        f"# DTC Dual SuperTrend Gate 백테스트 — {CATEGORY_LABEL.get(category, category)}",
        "",
        f"생성시각(UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## 알고리즘",
        "",
        "- ST_D: 일봉 SuperTrend(14,2)",
        "- ST_W: 주봉 SuperTrend(14,2), 현재 진행 중인 주 포함",
        "- CASE1: ST_D 상승 + 현재 ST_D >= 직전 하락 레그 마지막 ST_D",
        "- CASE2: ST_W 상승 + 현재 ST_W >= 직전 하락 레그 마지막 ST_W",
        "- CASE1 & CASE2: 매수",
        "- CASE1만: 단기 매수",
        "- CASE2만: 장기 매수",
        "- ST_D/ST_W 모두 하락: 매도",
        "- 둘 중 하나만 하락: 매도 고려",
        "- 나머지: HOLD",
        "- ADX(14,14)는 표에만 표시하며 의견 판정에는 사용하지 않음",
        "",
        "## 2년 매수→매도 Cycle Backtest",
        "",
        f"- **완료 사이클 최고수익률 중위값: {med_text}**",
        f"- 완료 사이클: {diag.get('completed_cycles', 0)}회 / 신호 사이클: {diag.get('signal_cycles', 0)}회",
        "- 진입: 포지션이 없을 때 최초 매수(CASE1&CASE2) 신호일 종가",
        "- 청산: 최초 매도(ST_D와 ST_W 모두 하락) 신호일 종가",
        "- 각 완료 사이클의 진입~청산 구간 최고 High 수익률을 구한 뒤 그 집합의 중위값",
        "- 미청산 사이클은 중위값에서 제외",
        "",
        "## 현재 의견 분포",
        "",
        "| 날짜 | 매수 | 단기 매수 | 장기 매수 | HOLD | 매도 고려 | 매도 | 합계 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in diag.get("current_opinion_distribution") or []:
        lines.append(
            f"| {row['date']} | {row['BUY']} | {row['SHORT_BUY']} | {row['LONG_BUY']} | {row['HOLD']} | "
            f"{row['SELL_CONSIDER']} | {row['SELL']} | {row['total']} |"
        )
    lines += [
        "",
        "## 주의",
        "",
        "- 현재 유니버스로 과거를 재구성하므로 생존 편향이 존재합니다.",
        "- 주봉 상태는 각 과거 일자 시점의 진행 중 주봉만 사용해 룩어헤드를 방지합니다.",
    ]
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
    print(f"DTC v14.4 Excel Dual-ST | {category} | mode={scan_mode} | universe={len(universe):,} | restricted={len(restricted):,}")
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

    # Price OHLC is exact-source only from v14.3 onward; v14.4 derives the live weekly bar from those same daily candles.  Do not apply the old
    # Yahoo missing-symbol quarantine to Toss/TradingView source requests.
    rejection["yahoo_quarantine"] = 0
    source_name = market_data_source_for(category)
    print(
        f"[{category}] exact-price universe={len(scan_universe):,} | source={source_name} "
        f"| KRX-stale={rejection['not_in_current_krx_snapshot']:,} "
        f"| size-prefilter={rejection['krx_market_size_lt_10t_prefilter'] + rejection['cached_small_quick_prefilter']:,}"
    )

    if category in {"US", "US_ETF"} and scan_universe:
        probe_error = ""
        for probe_attempt in range(1, 3):
            try:
                probe_ok, probe_total = exact_source_preflight(scan_universe, category, timeout=18)
                print(
                    f"[{category}] TradingView preflight OK: {probe_ok}/{probe_total} "
                    f"sample symbols returned candles"
                )
                probe_error = ""
                time.sleep(random.uniform(0.55, 0.95))
                break
            except Exception as exc:
                probe_error = f"{type(exc).__name__}: {exc}"
                print(
                    f"[{category}] TradingView preflight {probe_attempt}/2 failed: {probe_error}"
                )
                if probe_attempt < 2:
                    time.sleep(4.0)
        if probe_error:
            raise RuntimeError(
                f"{category} TradingView exact-source preflight failed twice; {probe_error}. "
                "Existing site data was not overwritten."
            )

    batches = list(chunks(scan_universe, BATCH_SIZE))
    total_batches = len(batches)
    consecutive_source_batch_failures = 0
    last_source_error = ""
    for batch_no, batch in enumerate(batches, 1):
        tickers = [s.ticker for s in batch]
        try:
            source_frames = download_market_frames(batch, category, bars=EXACT_HISTORY_BARS, timeout=46)
        except Exception as exc:
            last_source_error = f"{type(exc).__name__}: {exc}"
            print(f"[{category}] exact-source batch {batch_no}/{total_batches} failed: {last_source_error}")
            missing.extend(tickers)
            consecutive_source_batch_failures += 1
            # Three complete US batch failures represent a shared TradingView
            # transport/protocol problem, not 144 independent unavailable
            # symbols. Abort before hammering the public websocket with the
            # remaining universe and a second 500-symbol retry wave.
            if category in {"US", "US_ETF"} and consecutive_source_batch_failures >= 3:
                raise RuntimeError(
                    f"{category} TradingView exact-source unavailable across "
                    f"{consecutive_source_batch_failures} consecutive batches; "
                    f"last error: {last_source_error}. Existing site data was not overwritten."
                )
            time.sleep(1.8 if category in {"US", "US_ETF"} else 1.2)
            continue

        consecutive_source_batch_failures = 0
        for stock in batch:
            try:
                raw_frame = source_frames.get(stock.ticker)
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

        if batch_no % 5 == 0 or batch_no == total_batches:
            print(
                f"[{category}] {batch_no}/{total_batches} exact-source batches "
                f"({batch_no/max(1,total_batches)*100:5.1f}%) | priced={len(priced_tickers):,} | eligible={len(results):,}"
            )
        if batch_no != total_batches:
            if category in {"US", "US_ETF"}:
                time.sleep(random.uniform(0.45, 0.85))
            elif batch_no % 3 == 0:
                time.sleep(random.uniform(0.10, 0.28))

    retry = [t for t in dict.fromkeys(missing) if t not in priced_tickers and t in by_ticker]
    final_unavailable: list[str] = []
    if retry:
        print(f"[{category}] retrying {len(retry):,} exact-source unavailable symbols")
        remaining = retry
        attempts = 1 if scan_mode == "QUICK" else RETRY_ATTEMPTS
        for attempt in range(1, attempts + 1):
            if not remaining:
                break
            next_remaining: list[str] = []
            for ticker_batch in chunks(remaining, RETRY_BATCH_SIZE):
                stocks = [by_ticker[t] for t in ticker_batch]
                try:
                    source_frames = download_market_frames(stocks, category, bars=EXACT_HISTORY_BARS, timeout=55)
                except Exception as exc:
                    print(
                        f"[{category}] exact-source retry {attempt}/{attempts} batch failed "
                        f"({','.join(ticker_batch[:4])}{',...' if len(ticker_batch) > 4 else ''}): "
                        f"{type(exc).__name__}: {exc}"
                    )
                    next_remaining.extend(ticker_batch)
                    continue
                for ticker in ticker_batch:
                    stock = by_ticker[ticker]
                    raw_frame = source_frames.get(ticker)
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
            no_progress = bool(remaining and set(remaining) == previous)
            if no_progress:
                if attempt < attempts:
                    delay = min(20.0, 4.0 * (2 ** (attempt - 1)))
                    print(
                        f"[{category}] exact-source retry {attempt}/{attempts} made no progress "
                        f"({len(remaining):,}); backing off {delay:.0f}s before final retry"
                    )
                    time.sleep(delay)
                    continue
                print(f"[{category}] exact-source retry made no progress ({len(remaining):,}); stop")
                break
            if remaining and attempt < attempts:
                time.sleep(min(20.0, 4.0 * (2 ** (attempt - 1))))
        if remaining:
            final_unavailable = list(remaining)
            print(f"[{category}] final exact-source unavailable symbols={len(remaining):,}")

    expected_price_count = len(scan_universe)
    coverage = len(priced_tickers) / max(1, expected_price_count)
    required_coverage = MIN_COVERAGE[category]
    min_absolute = min(100, max(1, expected_price_count))
    if len(priced_tickers) < min_absolute or coverage < required_coverage:
        raise RuntimeError(
            f"{category} {source_name} coverage too low: {len(priced_tickers)}/{expected_price_count} "
            f"({coverage:.1%}), required>={required_coverage:.0%}. "
            "No Yahoo OHLC fallback is permitted; existing site data was not overwritten."
        )


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

    # Dual-ST opinion ranking: 매수 -> 단기/장기 매수 -> HOLD -> 매도 고려 -> 매도.
    # Same-level default is market cap/AUM descending.
    unsorted_items = list(results.values())
    backtest_refreshed = True
    backtest_diagnostics = _aggregate_supertrend_backtest(category, unsorted_items)

    def _rank_key(item: dict):
        level = int(item.get("rank_level", OPINION_ORDER["HOLD"]))
        cap = finite(item.get("market_size_krw"), -1.0)
        return (level, -cap, item.get("symbol", ""))

    items = sorted(unsorted_items, key=_rank_key)
    for rank, item in enumerate(items, 1):
        item["rank"] = rank

    top_items = items[:DISPLAY_META_TOP_N]

    # Sector enrichment remains limited to top cards. ETF size itself is already
    # collected for the full universe during analyze_prepared so market-cap/AUM
    # filtering works beyond TOP20.
    print(f"[{category}] display metadata enrichment: top {len(top_items):,}")
    toss_cache = _load_toss_symbol_cache()
    toss_cache_dirty = False
    for idx, item in enumerate(top_items, 1):
        stock = by_ticker.get(item["ticker"])
        if stock is None:
            continue
        enrich_display_metadata(stock, item, thresholds, size_cache)
        if stock.category in {"KR", "KR_ETF"}:
            toss_code = _direct_toss_product_code(item)
            if toss_code:
                item["toss_product_code"] = toss_code
                toss_cache_dirty = True
        # Keep detail metrics coherent with the summary display value.
        item["metrics"]["market_size_krw"] = item.get("market_size_krw")
        if idx % 20 == 0 or idx == len(top_items):
            print(f"[{category}] metadata {idx}/{len(top_items)}")
        # Cache normally makes this zero-cost after the first successful fetch.
        if _age_days((size_cache.get(item["ticker"]) or {}).get("meta_fetched_at")) < 0.01:
            time.sleep(random.uniform(0.12, 0.24))
    if toss_cache_dirty:
        _save_toss_symbol_cache(toss_cache)

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
        "strategy": "DUAL_ST_D_W_GATE_ST14_2_DUALSOURCE_V14_4",
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
        "yahoo_quarantine_count": 0,
        "market_data_source": source_name,
        "price_fallback": "DISABLED",
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
            "name": "Dual SuperTrend Gate",
            "supertrend_daily": "14,2",
            "supertrend_weekly": "14,2 (developing current week included)",
            "adx": "ADX(14,14), reference only",
            "opinion_order": ["BUY", "SHORT_BUY", "LONG_BUY", "HOLD", "SELL_CONSIDER", "SELL"],
            "case1": "ST_D UP and current ST_D >= last DOWN ST_D immediately before the latest DOWN->UP flip",
            "case2": "ST_W UP and current ST_W >= last DOWN ST_W immediately before the latest DOWN->UP flip",
            "buy": "CASE1 & CASE2",
            "short_buy": "CASE1 only while both timeframes remain UP",
            "long_buy": "CASE2 only while both timeframes remain UP",
            "sell": "ST_D DOWN and ST_W DOWN",
            "sell_consider": "exactly one of ST_D/ST_W is DOWN",
            "otherwise": "HOLD",
            "ranking": "매수 -> 단기/장기 매수 -> HOLD -> 매도 고려 -> 매도; same level market size descending",
            "ohlc_source": "Toss WTS c-chart for KR/KR_ETF; TradingView public chart websocket for US/US_ETF; no Yahoo fallback",
            "chart": "126 source-native daily candles + ST_D solid + live ST_W dashed; KR chart opens Toss, US chart opens TradingView",
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
        help="FULL refreshes market/quiz data; QUICK refreshes current dual-SuperTrend opinions and reuses the latest FULL quiz shard.",
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
