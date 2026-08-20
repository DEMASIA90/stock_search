from __future__ import annotations

import argparse
import hashlib
import json
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

MORNING_INVEST_COMPONENT_VERSION = "11.0"

import numpy as np
import pandas as pd
import yfinance as yf

from universe import Stock, fetch_kr_restricted_symbols, fetch_us_halted_symbols, get_universe

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------------------------------------------------------
# Dongtan Trading Center (DTC) scanner v11.0
# -----------------------------------------------------------------------------
# One unified 100-point score:
#   1) Bollinger lower-band proximity                           0~20
#   2) Current price inside dominant 7-bin volume zone, 40D   +20
#   3) Current price inside dominant 7-bin volume zone, 60D   +20
#   4) Current price inside dominant 7-bin volume zone, 120D  +20
#   5) Current price inside dominant 7-bin volume zone, 200D  +20
#
# For each lookback, the price range [min Low, max High] is split into 7 equal
# price zones. Each daily bar's volume is distributed across the zones in
# proportion to the overlap of [Low, High] with each zone. The single zone with
# the largest accumulated volume is the "dominant supply/volume zone". The +20
# condition is true when the current close is inside that dominant zone.
# -----------------------------------------------------------------------------

FULL_HISTORY_CALENDAR_DAYS = 1250
QUICK_HISTORY_CALENDAR_DAYS = 460
BATCH_SIZE = 24
RETRY_BATCH_SIZE = 4
DOWNLOAD_THREADS = 4
PRIMARY_BATCH_SLEEP = (0.55, 0.95)
RETRY_BATCH_SLEEP = (1.5, 2.8)
RETRY_ATTEMPTS = 3

BB_WINDOW = 20
BB_SIGMA = 2.0
PROFILE_BINS = 7
PROFILE_LOOKBACKS = (40, 60, 120, 200)
PROFILE_SCORE = 20.0
BOLLINGER_MAX_SCORE = 20.0
MAX_SCORE = 100.0
CHART_POINTS = 252

MIN_TRADING_DAYS = 250
MIN_PRICE_KRW = 1_000.0
MIN_MARKET_SIZE_KRW = 10_000_000_000_000.0  # equities only, inherited universe rule
ETF_CATEGORIES = {"KR_ETF", "US_ETF"}

# FULL backtest: historical signals >=60, next-day open entry, 60 trading-day exit.
BACKTEST_MIN_SCORE = 60.0
BACKTEST_LOOKBACK_DAYS = 252
BACKTEST_FORWARD_DAYS = 60
BACKTEST_COOLDOWN_DAYS = 10
BACKTEST_TOP_N = 100
BACKTEST_RECENT_TRADES = 10

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


def clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = pd.to_numeric(out["Close"], errors="coerce")
    out["BB_Mid"] = close.rolling(BB_WINDOW).mean()
    std = close.rolling(BB_WINDOW).std(ddof=0)
    out["BB_Upper"] = out["BB_Mid"] + BB_SIGMA * std
    out["BB_Lower"] = out["BB_Mid"] - BB_SIGMA * std
    width = (out["BB_Upper"] - out["BB_Lower"]).replace(0, np.nan)
    out["PercentB"] = (close - out["BB_Lower"]) / width
    return out


def bollinger_proximity_score(percent_b: float) -> float:
    """0~20 linear proximity score across the whole Bollinger channel.

    lower band / below (%B<=0) -> 20
    middle band (%B=0.5)       -> 10
    upper band / above (%B>=1) -> 0
    """
    if not np.isfinite(percent_b):
        return 0.0
    return round(BOLLINGER_MAX_SCORE * (1.0 - clip(float(percent_b), 0.0, 1.0)), 4)


def dominant_volume_zone(window: pd.DataFrame, current_price: float) -> dict:
    """Return the largest of seven volume-at-price zones for one lookback.

    A day's volume is distributed over every price zone touched by that day's
    [Low, High] range, proportional to overlap length. This is a daily-bar proxy
    for an intraday volume profile and is deterministic for both live scoring
    and point-in-time backtesting.
    """
    if window is None or window.empty or not np.isfinite(current_price):
        return {"available": False, "hit": False}

    lows = pd.to_numeric(window["Low"], errors="coerce").to_numpy(dtype=float)
    highs = pd.to_numeric(window["High"], errors="coerce").to_numpy(dtype=float)
    closes = pd.to_numeric(window["Close"], errors="coerce").to_numpy(dtype=float)
    volumes = pd.to_numeric(window["Volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)

    valid = np.isfinite(lows) & np.isfinite(highs) & np.isfinite(closes) & np.isfinite(volumes)
    valid &= (lows > 0) & (highs >= lows) & (volumes >= 0)
    if not valid.any():
        return {"available": False, "hit": False}

    lows, highs, closes, volumes = lows[valid], highs[valid], closes[valid], volumes[valid]
    pmin = float(np.nanmin(lows))
    pmax = float(np.nanmax(highs))
    if not (np.isfinite(pmin) and np.isfinite(pmax) and pmax >= pmin and pmin > 0):
        return {"available": False, "hit": False}

    values = np.zeros(PROFILE_BINS, dtype=float)

    if np.isclose(pmax, pmin):
        values[0] = float(np.nansum(volumes))
        lower = upper = center = pmin
        hit = bool(np.isclose(current_price, pmin))
        return {
            "available": True,
            "hit": hit,
            "index": 0,
            "lower": clean(lower),
            "upper": clean(upper),
            "center": clean(center),
            "dominant_volume": clean(values[0], 0),
            "dominant_share": 1.0 if values[0] > 0 else None,
        }

    edges = np.linspace(pmin, pmax, PROFILE_BINS + 1)

    # Vectorized 7-zone overlap allocation. This is identical to distributing
    # each bar in a Python loop, but is fast enough for the 60D backtest.
    spans = highs - lows
    ranged = spans > 1e-12
    if ranged.any():
        lo2 = lows[ranged, None]
        hi2 = highs[ranged, None]
        overlap = np.maximum(
            0.0,
            np.minimum(hi2, edges[1:][None, :]) - np.maximum(lo2, edges[:-1][None, :]),
        )
        weights = overlap / spans[ranged, None]
        values += np.sum(weights * volumes[ranged, None], axis=0)

    flat = ~ranged
    if flat.any():
        idxs = np.searchsorted(edges, closes[flat], side="right") - 1
        idxs = np.clip(idxs, 0, PROFILE_BINS - 1)
        np.add.at(values, idxs, volumes[flat])

    total = float(values.sum())
    if total <= 0:
        return {"available": False, "hit": False}

    idx = int(np.argmax(values))
    lower = float(edges[idx])
    upper = float(edges[idx + 1])
    center = float((lower + upper) / 2.0)
    tol = max(1e-12, abs(current_price) * 1e-10)
    hit = bool((current_price + tol) >= lower and (current_price - tol) <= upper)

    return {
        "available": True,
        "hit": hit,
        "index": idx,
        "lower": clean(lower),
        "upper": clean(upper),
        "center": clean(center),
        "dominant_volume": clean(values[idx], 0),
        "dominant_share": clean(values[idx] / total, 4),
    }


def score_at(frame: pd.DataFrame, ind: pd.DataFrame, pos: int) -> tuple[float, dict, dict]:
    if pos < max(BB_WINDOW - 1, max(PROFILE_LOOKBACKS) - 1) or pos >= len(frame):
        return 0.0, {}, {}

    close = finite(frame["Close"].iloc[pos])
    percent_b = finite(ind["PercentB"].iloc[pos])
    if not np.isfinite(close) or close <= 0 or not np.isfinite(percent_b):
        return 0.0, {}, {}

    s_bb = bollinger_proximity_score(percent_b)
    scores = {"bollinger": s_bb}
    profiles = {}

    total = s_bb
    for days in PROFILE_LOOKBACKS:
        window = frame.iloc[pos - days + 1 : pos + 1]
        profile = dominant_volume_zone(window, close)
        profile["days"] = days
        profiles[str(days)] = profile
        component = PROFILE_SCORE if profile.get("available") and profile.get("hit") else 0.0
        scores[f"profile_{days}"] = component
        total += component

    metrics = {
        "percent_b": clean(percent_b, 4),
        "bb_lower": clean(ind["BB_Lower"].iloc[pos]),
        "bb_mid": clean(ind["BB_Mid"].iloc[pos]),
        "bb_upper": clean(ind["BB_Upper"].iloc[pos]),
        "profiles": profiles,
    }
    return round(float(clip(total, 0.0, MAX_SCORE)), 4), scores, metrics


def thresholds_for(category: str, usdkrw: float | None) -> dict:
    if category in {"KR", "KR_ETF"}:
        return {"min_price": MIN_PRICE_KRW, "currency": "KRW", "usdkrw": None}
    if not usdkrw:
        raise RuntimeError("USD/KRW is required for US thresholds")
    return {"min_price": MIN_PRICE_KRW / usdkrw, "currency": "USD", "usdkrw": round(usdkrw, 4)}


def fetch_usdkrw() -> float:
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
            return fx
    except Exception as exc:
        print(f"USD/KRW lookup failed: {type(exc).__name__}: {exc}")
    # Safe operational fallback only for threshold conversion when Yahoo FX is unavailable.
    return 1400.0


def _make_chart(ind: pd.DataFrame, profiles: dict) -> dict:
    chart = ind.dropna(subset=["BB_Mid", "BB_Upper", "BB_Lower"]).tail(CHART_POINTS)
    profile_lines = []
    for days in PROFILE_LOOKBACKS:
        p = profiles.get(str(days)) or {}
        if p.get("available") and p.get("center") is not None:
            profile_lines.append({
                "days": days,
                "center": p.get("center"),
                "lower": p.get("lower"),
                "upper": p.get("upper"),
                "hit": bool(p.get("hit")),
            })
    return {
        "d": [pd.Timestamp(i).date().isoformat() for i in chart.index],
        "c": [clean(v) for v in chart["Close"]],
        "m": [clean(v) for v in chart["BB_Mid"]],
        "u": [clean(v) for v in chart["BB_Upper"]],
        "l": [clean(v) for v in chart["BB_Lower"]],
        "profiles": profile_lines,
    }


# -----------------------------------------------------------------------------
# Market-size / display metadata cache
# -----------------------------------------------------------------------------

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


def _fetch_stock_size_basis(stock: Stock) -> dict | None:
    if stock.category in ETF_CATEGORIES:
        return None
    ticker = yf.Ticker(stock.ticker)
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
    if stock.category in ETF_CATEGORIES:
        return np.nan, np.nan, "exempt"

    old = size_cache.get(stock.ticker) if isinstance(size_cache.get(stock.ticker), dict) else {}
    entry = old
    if not entry or _age_days(entry.get("fetched_at")) > STOCK_SHARES_CACHE_DAYS:
        fetched = None
        for attempt in range(1, MARKET_SIZE_RETRY_ATTEMPTS + 1):
            try:
                fetched = _fetch_stock_size_basis(stock)
                if fetched:
                    # Preserve display metadata fields that may have a different TTL.
                    for k in ("sector", "meta_fetched_at", "display_size_native", "display_size_basis"):
                        if k in old:
                            fetched[k] = old[k]
                    size_cache[stock.ticker] = fetched
                    entry = fetched
                    break
            except Exception as exc:
                if attempt == MARKET_SIZE_RETRY_ATTEMPTS:
                    print(f"[{stock.category}] size lookup failed {stock.ticker}: {type(exc).__name__}: {exc}")
                time.sleep(1.2 * attempt)

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
# Current analysis + 60-day backtest
# -----------------------------------------------------------------------------

def prepare_frame(raw_frame: pd.DataFrame, category: str, scan_mode: str) -> pd.DataFrame:
    frame = _numeric_ohlc(raw_frame)
    return completed_daily(frame, category, include_active_day=(scan_mode == "QUICK"))


def analyze_prepared(stock: Stock, frame: pd.DataFrame, thresholds: dict, size_cache: dict):
    if frame.empty:
        return None, "no_price"
    if len(frame) < MIN_TRADING_DAYS:
        return None, "listed_lt_250d"

    ind = add_indicators(frame)
    pos = len(frame) - 1
    close = finite(frame["Close"].iloc[pos])
    if not np.isfinite(close) or close < thresholds["min_price"]:
        return None, "price_lt_threshold"
    if not np.isfinite(finite(ind["PercentB"].iloc[pos])):
        return None, "indicator_history"

    if stock.category in ETF_CATEGORIES:
        market_size_native, market_size_krw, market_size_basis = np.nan, np.nan, "exempt"
    else:
        size_info = resolve_market_size(stock, close, thresholds, size_cache)
        if size_info is None:
            return None, "market_size_unavailable"
        market_size_native, market_size_krw, market_size_basis = size_info
        if market_size_krw < MIN_MARKET_SIZE_KRW:
            return None, "market_size_lt_10t"

    score, scores, metrics = score_at(frame, ind, pos)
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
        "score": round(score, 1),
        "display_score": round(score, 1),
        "scores": scores,
        "sector": "ETF" if stock.category in ETF_CATEGORIES else "—",
        "market_size_krw": clean(market_size_krw, 0),
        "market_size_basis": market_size_basis,
        "metrics": {
            **metrics,
            "market_size_native": clean(market_size_native, 0),
            "market_size_krw": clean(market_size_krw, 0),
            "market_size_basis": market_size_basis,
        },
        "backtest": {},
        "chart": _make_chart(ind, metrics.get("profiles") or {}),
    }
    return item, "passed"


def backtest_60d(frame: pd.DataFrame) -> dict:
    """Point-in-time backtest for the exact v11 score.

    Signal: score >= 60 at daily close.
    Entry: next trading-day open.
    Exit: close 60 trading sessions after entry.
    A 10-session cooldown limits near-duplicate signals from the same setup.
    """
    n = len(frame)
    min_pos = max(BB_WINDOW - 1, max(PROFILE_LOOKBACKS) - 1)
    if n < min_pos + BACKTEST_FORWARD_DAYS + 25:
        return {
            "available": False,
            "reason": "insufficient_history",
            "signals": 0,
            "avg_60d": None,
            "threshold": BACKTEST_MIN_SCORE,
        }

    ind = add_indicators(frame)
    last_signal_pos = -10_000
    # entry=i+1, exit=entry+60 must exist.
    last_i = n - BACKTEST_FORWARD_DAYS - 2
    start_i = max(min_pos, last_i - BACKTEST_LOOKBACK_DAYS + 1)

    trades = []
    for i in range(start_i, last_i + 1):
        if i - last_signal_pos < BACKTEST_COOLDOWN_DAYS:
            continue
        score, _, _ = score_at(frame, ind, i)
        if score < BACKTEST_MIN_SCORE:
            continue

        entry_i = i + 1
        exit_i = entry_i + BACKTEST_FORWARD_DAYS
        if exit_i >= n:
            continue
        entry = finite(frame["Open"].iloc[entry_i])
        exit_price = finite(frame["Close"].iloc[exit_i])
        if not (np.isfinite(entry) and entry > 0 and np.isfinite(exit_price) and exit_price > 0):
            continue
        ret60 = exit_price / entry - 1.0
        trades.append({
            "signal_date": pd.Timestamp(frame.index[i]).date().isoformat(),
            "entry_date": pd.Timestamp(frame.index[entry_i]).date().isoformat(),
            "exit_date": pd.Timestamp(frame.index[exit_i]).date().isoformat(),
            "score": round(score, 1),
            "entry": clean(entry),
            "exit": clean(exit_price),
            "ret_60d": clean(ret60, 5),
        })
        last_signal_pos = i

    returns = [finite(t["ret_60d"]) for t in trades]
    returns = [x for x in returns if np.isfinite(x)]
    avg = float(np.mean(returns)) if returns else np.nan
    med = float(np.median(returns)) if returns else np.nan
    win = float(np.mean([x > 0 for x in returns])) if returns else np.nan

    return {
        "available": True,
        "model": "score60_next_open_60tradingday_exit",
        "threshold": BACKTEST_MIN_SCORE,
        "lookback_signal_days": BACKTEST_LOOKBACK_DAYS,
        "forward_days": BACKTEST_FORWARD_DAYS,
        "cooldown_days": BACKTEST_COOLDOWN_DAYS,
        "signals": len(trades),
        "avg_60d": clean(avg, 5),
        "median_60d": clean(med, 5),
        "win_60d": clean(win, 4),
        "trades": trades[-BACKTEST_RECENT_TRADES:][::-1],
    }


# -----------------------------------------------------------------------------
# Yahoo batch download
# -----------------------------------------------------------------------------

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


def _compact_backtest(bt: dict) -> dict:
    return {
        "available": bool((bt or {}).get("available")),
        "signals": (bt or {}).get("signals"),
        "avg_60d": (bt or {}).get("avg_60d"),
        "median_60d": (bt or {}).get("median_60d"),
        "win_60d": (bt or {}).get("win_60d"),
        "threshold": (bt or {}).get("threshold", BACKTEST_MIN_SCORE),
        "preserved_from_full": bool((bt or {}).get("preserved_from_full")),
    }


def _summary_item(item: dict, detail_path: str) -> dict:
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
        "score": item["score"],
        "display_score": item.get("display_score", item["score"]),
        "scores": item.get("scores") or {},
        "sector": item.get("sector") or "—",
        "market_size_krw": item.get("market_size_krw"),
        "market_size_basis": item.get("market_size_basis"),
        "backtest": _compact_backtest(item.get("backtest") or {}),
        "detail_path": detail_path,
    }


def _write_category_site(category: str, payload_meta: dict, items: list[dict], size_cache: dict, scan_mode: str):
    category_dir = DATA_DIR / CATEGORY_DIR[category]

    previous_detail_payloads: dict[str, dict] = {}
    if scan_mode == "QUICK":
        previous_stocks = category_dir / "stocks"
        if previous_stocks.is_dir():
            for detail in previous_stocks.glob("*.json"):
                try:
                    payload = json.loads(detail.read_text(encoding="utf-8"))
                    ticker = str(payload.get("ticker") or "")
                    if ticker:
                        previous_detail_payloads[ticker] = payload
                except Exception:
                    pass
        print(f"[{category}] QUICK: previous details={len(previous_detail_payloads):,}")

    shutil.rmtree(category_dir, ignore_errors=True)
    stocks_dir = category_dir / "stocks"
    stocks_dir.mkdir(parents=True, exist_ok=True)

    summary_items = []
    for item in items:
        if scan_mode == "QUICK" and not (item.get("backtest") or {}).get("available"):
            previous = previous_detail_payloads.get(str(item.get("ticker"))) or {}
            prev_bt = previous.get("backtest") or {}
            if prev_bt.get("model") == "score60_next_open_60tradingday_exit" or prev_bt.get("avg_60d") is not None:
                item["backtest"] = dict(prev_bt)
                item["backtest"]["preserved_from_full"] = True

        filename = _detail_filename(item)
        relative = f"data/{CATEGORY_DIR[category]}/stocks/{filename}"
        summary_items.append(_summary_item(item, relative))
        (stocks_dir / filename).write_text(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )

    summary_payload = {
        **payload_meta,
        "storage_model": "summary_plus_lazy_stock_detail_dtc_v11",
        "detail_count": len(items),
        "items": summary_items,
    }
    summary_file = category_dir / "summary.json"
    summary_file.write_text(json.dumps(summary_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    sizes_file = category_dir / "sizes.json"
    sizes_file.write_text(json.dumps(size_cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    universe_snapshot = category_dir / "universe.json"
    root_cache = DATA_DIR / UNIVERSE_CACHE_FILE[category]
    if root_cache.is_file():
        shutil.copy2(root_cache, universe_snapshot)

    bundle_file = category_dir / "bundle.zip"
    with zipfile.ZipFile(bundle_file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(summary_file, "summary.json")
        zf.write(sizes_file, "sizes.json")
        if universe_snapshot.is_file():
            zf.write(universe_snapshot, "universe.json")
        for detail_file in sorted(stocks_dir.glob("*.json")):
            zf.write(detail_file, f"stocks/{detail_file.name}")

    return category_dir, len(items)


# -----------------------------------------------------------------------------
# Market scan
# -----------------------------------------------------------------------------

def scan_category(category: str, usdkrw: float | None = None, scan_mode: str = "FULL") -> None:
    universe, universe_source = get_universe(category)
    thresholds = thresholds_for(category, usdkrw)
    restricted, restriction_meta = _load_restrictions(category, universe)
    size_cache = _load_size_cache(category)

    print("=" * 76)
    print(f"DTC v11.0 | {category} | mode={scan_mode} | universe={len(universe):,} | restricted={len(restricted):,}")
    print("score = Bollinger 0~20 + dominant volume zones 40/60/120/200D each +20")
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
    print(f"[{category}] price-download universe={len(scan_universe):,}")

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
        time.sleep(random.uniform(*PRIMARY_BATCH_SLEEP))

    retry = [t for t in dict.fromkeys(missing) if t not in priced_tickers and t in by_ticker]
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

    items = sorted(results.values(), key=lambda x: (-float(x.get("score", 0.0)), x.get("symbol", "")))
    for rank, item in enumerate(items, 1):
        item["rank"] = rank

    top_items = items[:BACKTEST_TOP_N]
    top_tickers = {item["ticker"] for item in top_items}

    # FULL: expensive historical score reconstruction only for cards that are displayed.
    if scan_mode == "FULL":
        print(f"[{category}] 60D backtest: top {len(top_items):,} cards")
        for idx, item in enumerate(top_items, 1):
            frame = frames.get(item["ticker"])
            if frame is not None and not frame.empty:
                item["backtest"] = backtest_60d(frame)
            if idx % 20 == 0 or idx == len(top_items):
                print(f"[{category}] backtest {idx}/{len(top_items)}")

    # Sector / ETF size enrichment is also limited to displayed cards and cached.
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

    market_date = max((x["date"] for x in items if x.get("date")), default=None)
    payload_meta = {
        "app": "Dongtan Trading Center",
        "strategy": "DTC_V11_BB_VOLUME_PROFILE",
        "category": category,
        "category_label": CATEGORY_LABEL[category],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scan_mode": scan_mode,
        "data_status": "intraday_live" if scan_mode == "QUICK" else "close_confirmed",
        "backtest_refreshed": scan_mode == "FULL",
        "market_date": market_date,
        "universe_source": universe_source,
        "restriction_snapshot": restriction_meta,
        "universe_count": len(universe),
        "price_download_universe_count": expected_price_count,
        "priced_count": len(priced_tickers),
        "coverage_pct": round(coverage * 100, 1),
        "passed_count": len(items),
        "market_size_min_krw": None if category in ETF_CATEGORIES else MIN_MARKET_SIZE_KRW,
        "market_size_filter": "exempt" if category in ETF_CATEGORIES else "krw_10t_min",
        "market_size_lookup_coverage_pct": round(size_coverage * 100, 1) if np.isfinite(size_coverage) else None,
        "thresholds": thresholds,
        "filter_counts": dict(sorted(rejection.items())),
        "max_score": MAX_SCORE,
        "score_model": {
            "bollinger": {
                "weight": 20,
                "formula": "20*(1-clamp(percentB,0,1))",
                "lower_band": 20,
                "middle_band": 10,
                "upper_band": 0,
                "window": BB_WINDOW,
                "sigma": BB_SIGMA,
            },
            "volume_profile": {
                "lookbacks": list(PROFILE_LOOKBACKS),
                "bins": PROFILE_BINS,
                "score_each": PROFILE_SCORE,
                "condition": "current_close_inside_highest_volume_zone",
                "allocation": "daily_volume_distributed_by_low_high_overlap",
            },
        },
        "backtest_model": {
            "signal_score_gte": BACKTEST_MIN_SCORE,
            "entry": "next_trading_day_open",
            "exit": f"{BACKTEST_FORWARD_DAYS}_trading_days_after_entry_close",
            "signal_lookback_days": BACKTEST_LOOKBACK_DAYS,
            "cooldown_days": BACKTEST_COOLDOWN_DAYS,
            "computed_for_top_n": BACKTEST_TOP_N,
        },
    }

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
        help="FULL refreshes the score>=60 60-day backtest; QUICK refreshes current scores and keeps last FULL backtests.",
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

    usdkrw = fetch_usdkrw() if any(x.startswith("US") for x in markets) else None
    failures = []
    for category in markets:
        try:
            scan_category(category, usdkrw=usdkrw, scan_mode=args.scan_mode)
        except Exception as exc:
            failures.append((category, exc))
            print(f"ERROR {category}: {type(exc).__name__}: {exc}")
            traceback.print_exc()

    if failures:
        names = ", ".join(category for category, _ in failures)
        raise SystemExit(f"One or more categories failed: {names}")


if __name__ == "__main__":
    main()
