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

MORNING_INVEST_COMPONENT_VERSION = "11.9"

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from universe import Stock, fetch_kr_restricted_symbols, fetch_us_halted_symbols, get_universe
from trend_forecast import forecast as trend_forecast

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FX_CACHE_FILE = DATA_DIR / "fx_usdkrw.json"

# -----------------------------------------------------------------------------
# Dongtan Trading Center (DTC) scanner v12.0 · Forecast PJT 1
# -----------------------------------------------------------------------------
# Current setup score (0~10):
#   1) Bollinger lower-band proximity                                       0~1
#   2) Short volume-profile group (20/40/60D)                               0~3
#   3) Medium volume-profile group (80/100/150D)                            0~3
#   4) Long volume-profile group (200/300/400D)                             0~3
#
# A lookback's raw current-zone share is still retained, but the score uses
# current_zone_share / largest_zone_share.  This reaches 1 when the current
# price belongs to that lookback's dominant volume zone and avoids the old
# mathematical compression where a ten-bin share naturally sat near 0.1.
# Correlated lookbacks are averaged within short/medium/long groups so nine
# highly-overlapping windows do not count as nine independent signals.
#
# Ranking is by the CURRENT setup score.  Historical 60-session performance is
# a reference metric only. It is pooled across the current category and sampled
# at 60-session spacing within each stock to remove overlapping-return inflation.
# Total return uses Adj Close when available so dividends are reflected.
# -----------------------------------------------------------------------------

FULL_HISTORY_CALENDAR_DAYS = 1120
# QUICK does not rebuild the historical model when a recent FULL model exists,
# so only enough history for the current 400-session setup score is required.
QUICK_HISTORY_CALENDAR_DAYS = 720
BATCH_SIZE = 48
RETRY_BATCH_SIZE = 8
DOWNLOAD_THREADS = 8
# Successful batches are throttled only periodically (see scan loop) rather than
# after every request. Retries remain deliberately slower to avoid a 429 cascade.
PRIMARY_BATCH_SLEEP = (0.10, 0.28)
RETRY_BATCH_SLEEP = (0.70, 1.35)
RETRY_ATTEMPTS = 2

BB_WINDOW = 20
BB_SIGMA = 2.0
PROFILE_BINS = 10
PROFILE_LOOKBACKS = (20, 40, 60, 80, 100, 150, 200, 300, 400)
PROFILE_GROUPS = {
    "short": (20, 40, 60),
    "medium": (80, 100, 150),
    "long": (200, 300, 400),
}
PROFILE_GROUP_WEIGHT = 3.0
LOWER_SUPPORT_GROUP_WEIGHT = 1.0
BOLLINGER_MAX_SCORE = 1.0
# Raw profile-analysis score: BB 1 + current-zone concentration 9 + lower-support 3 = 13.
PROFILE_RAW_MAX_SCORE = (
    BOLLINGER_MAX_SCORE
    + PROFILE_GROUP_WEIGHT * len(PROFILE_GROUPS)
    + LOWER_SUPPORT_GROUP_WEIGHT * len(PROFILE_GROUPS)
)
MAX_SCORE = 10.0
CHART_POINTS = 63  # ~3 trading months
RSI_WINDOW = 14
SIGNAL_LOOKBACK = 20
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

# Restored to the pre-v12 value. score_at() returns (0.0, {}, {}) below pos=399,
# so a lower gate admits stocks that cannot produce a setup score or volume
# profile. Forecast PJT 1 needs only 272 sessions, so 400 never blocks it.
MIN_TRADING_DAYS = 400
MIN_PRICE_KRW = 1_000.0
MIN_MARKET_SIZE_KRW = 10_000_000_000_000.0  # equities only, inherited universe rule
ETF_CATEGORIES = {"KR_ETF", "US_ETF"}

BACKTEST_FORWARD_DAYS = 60
BACKTEST_RECENT_TRADES = 10
BACKTEST_NON_OVERLAP_STEP = 60
BACKTEST_TARGET_POOL_SAMPLES = 40
BACKTEST_MAX_BAND_HALF_WIDTH = 2.0
BACKTEST_MODEL_GRID_STEP = 0.1
BACKTEST_MODEL_MAX_AGE_DAYS = 10
DISPLAY_META_TOP_N = 100

# Gwangju Fortune Teller (광주점쟁이) linear volume-anchor forecast.
GWANGJU_LOOKBACKS = {
    "1y": 252,
    "6m": 126,
    "3m": 63,
}
GWANGJU_WEIGHTS = {
    "1y": 0.2,
    "6m": 0.3,
    "3m": 0.5,
}
GWANGJU_HORIZONS = (5, 20, 60)
GWANGJU_BACKTEST_DAYS = 252
GWANGJU_BACKTEST_TOP_N = 100

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
    if "Adj Close" in out.columns:
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


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = pd.to_numeric(out["Close"], errors="coerce")
    out["BB_Mid"] = close.rolling(BB_WINDOW).mean()
    std = close.rolling(BB_WINDOW).std(ddof=0)
    out["BB_Upper"] = out["BB_Mid"] + BB_SIGMA * std
    out["BB_Lower"] = out["BB_Mid"] - BB_SIGMA * std
    width = (out["BB_Upper"] - out["BB_Lower"]).replace(0, np.nan)
    out["PercentB"] = (close - out["BB_Lower"]) / width

    # Pullback confirmation indicator. Wilder-style RSI via exponential smoothing.
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / RSI_WINDOW, adjust=False, min_periods=RSI_WINDOW).mean()
    avg_loss = loss.ewm(alpha=1.0 / RSI_WINDOW, adjust=False, min_periods=RSI_WINDOW).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    valid = avg_gain.notna() & avg_loss.notna()
    both_flat = valid & (avg_gain == 0) & (avg_loss == 0)
    rsi = rsi.where(~(valid & (avg_loss == 0)), 100.0)
    rsi = rsi.where(~(valid & (avg_gain == 0)), 0.0)
    rsi = rsi.mask(both_flat, 50.0)
    rsi = rsi.where(valid, np.nan)
    out["RSI14"] = rsi
    return out


def trade_signal_metrics(frame: pd.DataFrame, ind: pd.DataFrame, pos: int) -> dict:
    """Compact raw indicators shown on each card for pullback/breakout judgment."""
    if pos < SIGNAL_LOOKBACK or pos >= len(frame):
        return {"pullback": {}, "breakout": {}}

    close = finite(frame["Close"].iloc[pos])
    percent_b = finite(ind["PercentB"].iloc[pos])
    rsi14 = finite(ind["RSI14"].iloc[pos])

    # Exclude the current bar so a breakout reads positive once price clears the
    # resistance that existed before today's session.
    prior = frame.iloc[pos - SIGNAL_LOOKBACK : pos]
    prior_high = finite(pd.to_numeric(prior["High"], errors="coerce").max())
    prior_volume = pd.to_numeric(prior["Volume"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    avg_volume = finite(prior_volume.mean()) if not prior_volume.empty else np.nan
    current_volume = finite(frame["Volume"].iloc[pos])

    high_gap_pct = ((close / prior_high) - 1.0) * 100.0 if np.isfinite(close) and np.isfinite(prior_high) and prior_high > 0 else np.nan
    volume_ratio = current_volume / avg_volume if np.isfinite(current_volume) and np.isfinite(avg_volume) and avg_volume > 0 else np.nan

    return {
        "pullback": {
            "percent_b": clean(percent_b, 4),
            "rsi14": clean(rsi14, 1),
        },
        "breakout": {
            "prior_20d_high": clean(prior_high),
            "high20_gap_pct": clean(high_gap_pct, 2),
            "volume_ratio_20d": clean(volume_ratio, 2),
        },
    }


def bollinger_proximity_score(percent_b: float) -> float:
    """0~1 linear proximity score across the Bollinger channel.

    lower band / below (%B<=0) -> 1
    middle band (%B=0.5)       -> 0.5
    upper band / above (%B>=1) -> 0
    """
    if not np.isfinite(percent_b):
        return 0.0
    return round(BOLLINGER_MAX_SCORE * (1.0 - clip(float(percent_b), 0.0, 1.0)), 6)

def _profile_numpy(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (
        pd.to_numeric(frame["Low"], errors="coerce").to_numpy(dtype=float),
        pd.to_numeric(frame["High"], errors="coerce").to_numpy(dtype=float),
        pd.to_numeric(frame["Close"], errors="coerce").to_numpy(dtype=float),
        pd.to_numeric(frame["Volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float),
    )


def _current_price_volume_zone_arrays(
    lows: np.ndarray,
    highs: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    current_price: float,
) -> dict:
    if len(lows) == 0 or not np.isfinite(current_price):
        return {"available": False, "share": 0.0}

    valid = np.isfinite(lows) & np.isfinite(highs) & np.isfinite(closes) & np.isfinite(volumes)
    valid &= (lows > 0) & (highs >= lows) & (volumes >= 0)
    if not valid.any():
        return {"available": False, "share": 0.0}

    lows, highs, closes, volumes = lows[valid], highs[valid], closes[valid], volumes[valid]
    pmin = float(np.nanmin(lows))
    pmax = float(np.nanmax(highs))
    if not (np.isfinite(pmin) and np.isfinite(pmax) and pmax >= pmin and pmin > 0):
        return {"available": False, "share": 0.0}

    values = np.zeros(PROFILE_BINS, dtype=float)
    if np.isclose(pmax, pmin):
        total = float(np.nansum(volumes))
        share = 1.0 if total > 0 else 0.0
        return {
            "available": total > 0,
            "index": 0,
            "lower": clean(pmin),
            "upper": clean(pmax),
            "center": clean(pmin),
            "zone_volume": clean(total, 0),
            "total_volume": clean(total, 0),
            "share": clean(share, 6),
            "max_share": clean(share, 6),
            "relative_to_peak": clean(1.0 if total > 0 else 0.0, 6),
            "bins": PROFILE_BINS,
        }

    edges = np.linspace(pmin, pmax, PROFILE_BINS + 1)
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
        return {"available": False, "share": 0.0}

    idx = int(np.searchsorted(edges, current_price, side="right") - 1)
    idx = int(np.clip(idx, 0, PROFILE_BINS - 1))
    lower = float(edges[idx])
    upper = float(edges[idx + 1])
    center = float((lower + upper) / 2.0)
    zone_volume = float(values[idx])
    share = zone_volume / total
    max_zone_volume = float(np.max(values)) if len(values) else 0.0
    max_share = max_zone_volume / total if total > 0 else 0.0
    relative_to_peak = share / max_share if max_share > 0 else 0.0
    # New support component: volume strictly below the current price zone / all
    # ten-zone volume. The current zone is excluded from the numerator but remains
    # in the denominator, exactly matching the scoring definition.
    lower_zone_volume = float(np.sum(values[:idx])) if idx > 0 else 0.0
    lower_volume_ratio = lower_zone_volume / total if total > 0 else 0.0

    return {
        "available": True,
        "index": idx,
        "lower": clean(lower),
        "upper": clean(upper),
        "center": clean(center),
        "zone_volume": clean(zone_volume, 0),
        "total_volume": clean(total, 0),
        "share": clean(share, 6),
        "max_share": clean(max_share, 6),
        "relative_to_peak": clean(clip(relative_to_peak, 0.0, 1.0), 6),
        "lower_zone_volume": clean(lower_zone_volume, 0),
        "lower_volume_ratio": clean(clip(lower_volume_ratio, 0.0, 1.0), 6),
        "bins": PROFILE_BINS,
    }


def current_price_volume_zone(window: pd.DataFrame, current_price: float) -> dict:
    """Public DataFrame wrapper for the optimized ten-bin volume-profile core."""
    if window is None or window.empty or not np.isfinite(current_price):
        return {"available": False, "share": 0.0}
    return _current_price_volume_zone_arrays(*_profile_numpy(window), current_price)


def score_at(
    frame: pd.DataFrame,
    ind: pd.DataFrame,
    pos: int,
    profile_arrays: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None,
) -> tuple[float, dict, dict]:
    """Calculate the normalized 0~10 volume-profile setup score.

    Raw score (max 13):
      * Bollinger lower-band proximity: 0~1
      * Current-zone concentration, grouped short/mid/long: 0~3 each (0~9)
      * Volume below current zone / all ten zones, grouped short/mid/long: 0~1 each (0~3)

    The raw 0~13 score is finally normalized to 0~10.
    """
    if pos < max(BB_WINDOW - 1, max(PROFILE_LOOKBACKS) - 1) or pos >= len(frame):
        return 0.0, {}, {}

    close = finite(frame["Close"].iloc[pos])
    percent_b = finite(ind["PercentB"].iloc[pos])
    if not np.isfinite(close) or close <= 0 or not np.isfinite(percent_b):
        return 0.0, {}, {}

    s_bb = bollinger_proximity_score(percent_b)
    scores = {"bollinger": s_bb}
    profiles = {}
    concentration_components: dict[int, float] = {}
    lower_support_components: dict[int, float] = {}

    if profile_arrays is None:
        profile_arrays = _profile_numpy(frame)
    lows_all, highs_all, closes_all, volumes_all = profile_arrays

    for days in PROFILE_LOOKBACKS:
        start = pos - days + 1
        profile = _current_price_volume_zone_arrays(
            lows_all[start : pos + 1],
            highs_all[start : pos + 1],
            closes_all[start : pos + 1],
            volumes_all[start : pos + 1],
            close,
        )
        profile["days"] = days
        profiles[str(days)] = profile
        concentration = finite(profile.get("relative_to_peak"), 0.0) if profile.get("available") else 0.0
        lower_support = finite(profile.get("lower_volume_ratio"), 0.0) if profile.get("available") else 0.0
        concentration_components[days] = float(clip(concentration, 0.0, 1.0))
        lower_support_components[days] = float(clip(lower_support, 0.0, 1.0))

    raw_total = s_bb
    profile_groups = {}
    lower_support_groups = {}
    for group_name, group_days in PROFILE_GROUPS.items():
        concentration_values = [concentration_components.get(days, 0.0) for days in group_days]
        concentration_mean = float(np.mean(concentration_values)) if concentration_values else 0.0
        concentration_score = PROFILE_GROUP_WEIGHT * concentration_mean
        scores[f"profile_{group_name}"] = round(concentration_score, 6)
        raw_total += concentration_score

        support_values = [lower_support_components.get(days, 0.0) for days in group_days]
        support_mean = float(np.mean(support_values)) if support_values else 0.0
        support_score = LOWER_SUPPORT_GROUP_WEIGHT * support_mean
        scores[f"lower_support_{group_name}"] = round(support_score, 6)
        raw_total += support_score

        profile_groups[group_name] = {
            "lookbacks": list(group_days),
            "mean_relative_to_peak": clean(concentration_mean, 6),
            "score": scores[f"profile_{group_name}"],
            "weight": PROFILE_GROUP_WEIGHT,
        }
        lower_support_groups[group_name] = {
            "lookbacks": list(group_days),
            "mean_lower_volume_ratio": clean(support_mean, 6),
            "score": scores[f"lower_support_{group_name}"],
            "weight": LOWER_SUPPORT_GROUP_WEIGHT,
        }

    normalized = float(clip(raw_total / PROFILE_RAW_MAX_SCORE * MAX_SCORE, 0.0, MAX_SCORE))
    metrics = {
        "percent_b": clean(percent_b, 4),
        "bb_lower": clean(ind["BB_Lower"].iloc[pos]),
        "bb_mid": clean(ind["BB_Mid"].iloc[pos]),
        "bb_upper": clean(ind["BB_Upper"].iloc[pos]),
        "profiles": profiles,
        "profile_groups": profile_groups,
        "lower_support_groups": lower_support_groups,
        "raw_score": clean(raw_total, 6),
        "raw_score_max": PROFILE_RAW_MAX_SCORE,
        "normalized_score": clean(normalized, 6),
    }
    return round(normalized, 6), scores, metrics


def _heikin_ashi_frame(ohlc: pd.DataFrame) -> pd.DataFrame:
    """Build Heikin-Ashi candles from an already-aggregated OHLC frame."""
    if ohlc is None or ohlc.empty:
        return pd.DataFrame(columns=["HA_Open", "HA_High", "HA_Low", "HA_Close", "Bullish"], index=[])
    o = pd.to_numeric(ohlc["Open"], errors="coerce").to_numpy(dtype=float)
    h = pd.to_numeric(ohlc["High"], errors="coerce").to_numpy(dtype=float)
    l = pd.to_numeric(ohlc["Low"], errors="coerce").to_numpy(dtype=float)
    c = pd.to_numeric(ohlc["Close"], errors="coerce").to_numpy(dtype=float)
    n = len(ohlc)
    ha_close = (o + h + l + c) / 4.0
    ha_open = np.empty(n, dtype=float)
    if n:
        ha_open[0] = (o[0] + c[0]) / 2.0
        for i in range(1, n):
            ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0
    ha_high = np.maximum.reduce([h, ha_open, ha_close]) if n else np.array([], dtype=float)
    ha_low = np.minimum.reduce([l, ha_open, ha_close]) if n else np.array([], dtype=float)
    out = pd.DataFrame({
        "HA_Open": ha_open,
        "HA_High": ha_high,
        "HA_Low": ha_low,
        "HA_Close": ha_close,
    }, index=ohlc.index)
    out["Bullish"] = out["HA_Close"] > out["HA_Open"]
    return out


def _aggregate_ohlc_for_ha(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    """Aggregate original daily OHLC first, then HA is calculated on that timeframe."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    idx = pd.DatetimeIndex(frame.index)
    key = idx.to_period(period)
    tmp = frame[["Open", "High", "Low", "Close"]].copy()
    tmp["_period"] = key
    grouped = tmp.groupby("_period", sort=True)
    out = pd.DataFrame({
        "Open": grouped["Open"].first(),
        "High": grouped["High"].max(),
        "Low": grouped["Low"].min(),
        "Close": grouped["Close"].last(),
    })
    return out.dropna(subset=["Open", "High", "Low", "Close"])


def candle_analysis_score(frame: pd.DataFrame, pos: int | None = None) -> tuple[float, dict]:
    """Heikin-Ashi candle-analysis score, normalized to 0~10.

    Raw max 4:
      * Weekly + monthly HA both bullish = 1.0; monthly only = 0.5; otherwise 0
      * Latest daily HA bullish: 3 * bearish count in prior 20 sessions / 20; if latest HA bearish = 0
    The active current week/month is included because aggregation uses all rows through ``pos``.
    """
    if frame is None or frame.empty:
        return 0.0, {}
    if pos is None:
        pos = len(frame) - 1
    if pos < 20 or pos >= len(frame):
        return 0.0, {}
    hist = frame.iloc[: pos + 1][["Open", "High", "Low", "Close"]].copy()
    daily_ha = _heikin_ashi_frame(hist)
    if len(daily_ha) < 21:
        return 0.0, {}
    today_bullish = bool(daily_ha["Bullish"].iloc[-1])
    prior20 = daily_ha["Bullish"].iloc[-21:-1]
    bearish20 = int((~prior20.astype(bool)).sum())
    daily_raw = (3.0 * bearish20 / 20.0) if today_bullish else 0.0

    weekly_ha = _heikin_ashi_frame(_aggregate_ohlc_for_ha(hist, "W-FRI"))
    monthly_ha = _heikin_ashi_frame(_aggregate_ohlc_for_ha(hist, "M"))
    weekly_bullish = bool(weekly_ha["Bullish"].iloc[-1]) if not weekly_ha.empty else False
    monthly_bullish = bool(monthly_ha["Bullish"].iloc[-1]) if not monthly_ha.empty else False
    trend_raw = 1.0 if (weekly_bullish and monthly_bullish) else (0.5 if monthly_bullish else 0.0)

    raw_total = trend_raw + daily_raw
    normalized = float(clip(raw_total / 4.0 * MAX_SCORE, 0.0, MAX_SCORE))
    metrics = {
        "weekly_bullish": weekly_bullish,
        "monthly_bullish": monthly_bullish,
        "today_daily_bullish": today_bullish,
        "prior20_bearish_count": bearish20,
        "trend_raw_score": clean(trend_raw, 4),
        "daily_raw_score": clean(daily_raw, 4),
        "raw_score": clean(raw_total, 4),
        "raw_score_max": 4.0,
        "normalized_score": clean(normalized, 4),
    }
    return round(normalized, 6), metrics

def _gwangju_top2_slope_arrays(
    closes: np.ndarray,
    volumes: np.ndarray,
    dates: pd.DatetimeIndex,
    pos: int,
    lookback: int,
) -> tuple[float, dict]:
    """Slope through closes of the two highest-volume sessions in a lookback.

    X is the trading-session index, so the slope unit is native price per
    trading day.  The current session is included when ``pos`` points to it.
    """
    start = max(0, pos - lookback + 1)
    if pos - start + 1 < 2:
        return np.nan, {}
    c = closes[start : pos + 1]
    v = volumes[start : pos + 1]
    valid = np.isfinite(c) & (c > 0) & np.isfinite(v) & (v >= 0)
    idx = np.flatnonzero(valid)
    if idx.size < 2:
        return np.nan, {}
    vv = v[idx]
    if not np.isfinite(vv).any() or float(np.nanmax(vv)) <= 0:
        return np.nan, {}
    # argpartition avoids a full sort on every historical evaluation anchor.
    take = np.argpartition(vv, -2)[-2:]
    local = idx[take]
    abs_idx = np.sort(local + start)
    i1, i2 = int(abs_idx[0]), int(abs_idx[1])
    if i2 <= i1:
        return np.nan, {}
    c1, c2 = float(closes[i1]), float(closes[i2])
    slope = (c2 - c1) / float(i2 - i1)
    meta = {
        "lookback": int(lookback),
        "first": {
            "date": pd.Timestamp(dates[i1]).date().isoformat(),
            "close": clean(c1, 6),
            "volume": clean(volumes[i1], 0),
        },
        "second": {
            "date": pd.Timestamp(dates[i2]).date().isoformat(),
            "close": clean(c2, 6),
            "volume": clean(volumes[i2], 0),
        },
        "trading_day_gap": int(i2 - i1),
        "slope_price_per_day": clean(slope, 8),
    }
    return float(slope), meta


def gwangju_prediction(frame: pd.DataFrame, pos: int | None = None) -> dict:
    """Current 광주점쟁이 forecast from three volume-anchor slopes.

    m1: 1Y top-2-volume close slope
    m2: 6M top-2-volume close slope
    m3: 3M top-2-volume close slope
    m0 = 0.2*m1 + 0.3*m2 + 0.5*m3

    The forecast line is re-anchored at the evaluation day's close and extended
    linearly in trading-day units.  The displayed score is the arithmetic mean
    of predicted 5D/20D/60D price returns, in percent.
    """
    if frame is None or frame.empty:
        return {"available": False}
    if pos is None:
        pos = len(frame) - 1
    if pos < max(GWANGJU_LOOKBACKS.values()) - 1 or pos >= len(frame):
        return {"available": False, "reason": "insufficient_history"}

    closes = pd.to_numeric(frame["Close"], errors="coerce").to_numpy(dtype=float)
    volumes = pd.to_numeric(frame["Volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    dates = pd.DatetimeIndex(frame.index)
    anchor = float(closes[pos]) if np.isfinite(closes[pos]) else np.nan
    if not np.isfinite(anchor) or anchor <= 0:
        return {"available": False, "reason": "invalid_close"}

    slopes: dict[str, float] = {}
    anchors: dict[str, dict] = {}
    for key, lookback in GWANGJU_LOOKBACKS.items():
        slope, meta = _gwangju_top2_slope_arrays(closes, volumes, dates, pos, lookback)
        if not np.isfinite(slope):
            return {"available": False, "reason": f"{key}_slope_unavailable"}
        slopes[key] = float(slope)
        anchors[key] = meta

    m1, m2, m3 = slopes["1y"], slopes["6m"], slopes["3m"]
    m0 = GWANGJU_WEIGHTS["1y"] * m1 + GWANGJU_WEIGHTS["6m"] * m2 + GWANGJU_WEIGHTS["3m"] * m3
    predicted_prices = {h: anchor + m0 * h for h in GWANGJU_HORIZONS}
    predicted_returns = {h: (predicted_prices[h] / anchor - 1.0) * 100.0 for h in GWANGJU_HORIZONS}
    score = float(np.mean([predicted_returns[h] for h in GWANGJU_HORIZONS]))
    return {
        "available": True,
        "anchor_date": pd.Timestamp(dates[pos]).date().isoformat(),
        "anchor_close": clean(anchor, 6),
        "m1": clean(m1, 8),
        "m2": clean(m2, 8),
        "m3": clean(m3, 8),
        "m0": clean(m0, 8),
        "m0_pct_per_day": clean(m0 / anchor * 100.0, 6),
        "weights": {"m1": 0.2, "m2": 0.3, "m3": 0.5},
        "volume_anchors": anchors,
        "predicted_price_5d": clean(predicted_prices[5], 6),
        "predicted_price_20d": clean(predicted_prices[20], 6),
        "predicted_price_60d": clean(predicted_prices[60], 6),
        "predicted_return_5d_pct": clean(predicted_returns[5], 4),
        "predicted_return_20d_pct": clean(predicted_returns[20], 4),
        "predicted_return_60d_pct": clean(predicted_returns[60], 4),
        "score_pct": clean(score, 4),
        "forecast_horizon_trading_days": 60,
    }


def gwangju_backtest(frame: pd.DataFrame) -> dict:
    """One-year walk-forward forecast error for the 광주점쟁이 method.

    Every evaluable trading day in the trailing year is an anchor. Each anchor
    uses only information available on that date. Error is prediction minus
    actual close-to-close return; MAE is reported in percentage points.
    """
    if frame is None or frame.empty:
        return {"available": False}
    n = len(frame)
    max_h = max(GWANGJU_HORIZONS)
    min_pos = max(GWANGJU_LOOKBACKS.values()) - 1
    last_anchor = n - max_h - 1
    if last_anchor < min_pos:
        return {"available": False, "reason": "insufficient_history"}
    first_anchor = max(min_pos, last_anchor - GWANGJU_BACKTEST_DAYS + 1)

    closes = pd.to_numeric(frame["Close"], errors="coerce").to_numpy(dtype=float)
    volumes = pd.to_numeric(frame["Volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    dates = pd.DatetimeIndex(frame.index)
    errors = {h: [] for h in GWANGJU_HORIZONS}
    preds = {h: [] for h in GWANGJU_HORIZONS}
    actuals = {h: [] for h in GWANGJU_HORIZONS}
    anchor_count = 0

    for pos in range(first_anchor, last_anchor + 1):
        anchor = closes[pos]
        if not np.isfinite(anchor) or anchor <= 0:
            continue
        parts = []
        valid = True
        for key, lookback in GWANGJU_LOOKBACKS.items():
            slope, _ = _gwangju_top2_slope_arrays(closes, volumes, dates, pos, lookback)
            if not np.isfinite(slope):
                valid = False
                break
            parts.append((key, slope))
        if not valid:
            continue
        slope_map = dict(parts)
        m0 = (
            GWANGJU_WEIGHTS["1y"] * slope_map["1y"]
            + GWANGJU_WEIGHTS["6m"] * slope_map["6m"]
            + GWANGJU_WEIGHTS["3m"] * slope_map["3m"]
        )
        ok = True
        local = {}
        for h in GWANGJU_HORIZONS:
            future = closes[pos + h]
            if not np.isfinite(future) or future <= 0:
                ok = False
                break
            pred = (m0 * h / anchor) * 100.0
            actual = (future / anchor - 1.0) * 100.0
            local[h] = (pred, actual, pred - actual)
        if not ok:
            continue
        anchor_count += 1
        for h, (pred, actual, err) in local.items():
            preds[h].append(pred)
            actuals[h].append(actual)
            errors[h].append(err)

    if anchor_count == 0:
        return {"available": False, "reason": "no_valid_anchors"}

    out = {
        "available": True,
        "method": "walk_forward_daily_close",
        "anchor_count": int(anchor_count),
        "window_trading_days": GWANGJU_BACKTEST_DAYS,
        "error_definition": "predicted_return_pct - actual_return_pct",
        "return_basis": "Close",
    }
    maes = []
    for h in GWANGJU_HORIZONS:
        e = np.asarray(errors[h], dtype=float)
        p = np.asarray(preds[h], dtype=float)
        a = np.asarray(actuals[h], dtype=float)
        mae = float(np.mean(np.abs(e)))
        maes.append(mae)
        out[f"mae_{h}d_pctp"] = clean(mae, 4)
        out[f"bias_{h}d_pctp"] = clean(np.mean(e), 4)
        out[f"pred_avg_{h}d_pct"] = clean(np.mean(p), 4)
        out[f"actual_avg_{h}d_pct"] = clean(np.mean(a), 4)
    out["mean_mae_pctp"] = clean(float(np.mean(maes)), 4)
    return out


def _load_cached_gwangju_backtests(category: str) -> dict[str, dict]:
    path = DATA_DIR / CATEGORY_DIR[category] / "summary.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        out = {}
        for row in payload.get("items") or []:
            ticker = str(row.get("ticker") or "")
            bt = row.get("gwangju_backtest") or {}
            if ticker and bt.get("available"):
                out[ticker] = bt
        return out
    except Exception:
        return {}


def attach_gwangju_backtests(items: list[dict], frames: dict[str, pd.DataFrame]) -> None:
    """Compute walk-forward error only for the strongest current forecasts.

    This keeps QUICK/FULL runtime bounded while fully covering the default TOP20
    and a large search buffer. Current forecasts are still calculated for every
    eligible item and therefore ranking itself is complete.
    """
    ranked = sorted(
        items,
        key=lambda x: -finite((x.get("gwangju_prediction") or {}).get("score_pct"), -1e18),
    )
    for item in items:
        item["gwangju_backtest"] = {"available": False, "reason": "not_in_backtest_top_buffer"}
    for item in ranked[:GWANGJU_BACKTEST_TOP_N]:
        frame = frames.get(item.get("ticker"))
        if frame is None or frame.empty:
            continue
        item["gwangju_backtest"] = gwangju_backtest(frame)


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


def _make_chart(ind: pd.DataFrame, profiles: dict) -> dict:
    chart = ind.dropna(subset=["BB_Mid", "BB_Upper", "BB_Lower"]).tail(CHART_POINTS).copy()
    # Forecast PJT 1 uses adjusted prices. Scale OHLC by AdjClose/Close when the
    # adjusted series is available so the visible candle level matches the model.
    if "Adj Close" in chart.columns:
        raw_close = pd.to_numeric(chart["Close"], errors="coerce")
        adj_close = pd.to_numeric(chart["Adj Close"], errors="coerce")
        factor = (adj_close / raw_close.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    else:
        factor = pd.Series(1.0, index=chart.index)
    chart_o = pd.to_numeric(chart["Open"], errors="coerce") * factor
    chart_h = pd.to_numeric(chart["High"], errors="coerce") * factor
    chart_l = pd.to_numeric(chart["Low"], errors="coerce") * factor
    chart_c = pd.to_numeric(chart["Close"], errors="coerce") * factor
    profile_lines = []
    for days in PROFILE_LOOKBACKS:
        p = profiles.get(str(days)) or {}
        if p.get("available") and p.get("center") is not None:
            profile_lines.append({
                "days": days,
                "center": p.get("center"),
                "lower": p.get("lower"),
                "upper": p.get("upper"),
                "share": p.get("share"),
                "index": p.get("index"),
            })
    return {
        "d": [pd.Timestamp(i).date().isoformat() for i in chart.index],
        "o": [clean(v) for v in chart_o],
        "h": [clean(v) for v in chart_h],
        "lo": [clean(v) for v in chart_l],
        "c": [clean(v) for v in chart_c],
        # Bands are computed on raw Close; scale them with the same factor as the
        # candles or the two series sit on different price levels for any name
        # with dividends/splits.
        "m": [clean(v) for v in pd.to_numeric(chart["BB_Mid"], errors="coerce") * factor],
        "u": [clean(v) for v in pd.to_numeric(chart["BB_Upper"], errors="coerce") * factor],
        "l": [clean(v) for v in pd.to_numeric(chart["BB_Lower"], errors="coerce") * factor],
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


def _backtest_model_path(category: str) -> Path:
    return _category_data_dir(category) / "backtest_model.json"


def _load_backtest_model(category: str) -> dict | None:
    path = _backtest_model_path(category)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("grid"), dict):
        return None
    if _age_days(payload.get("generated_at_utc")) > BACKTEST_MODEL_MAX_AGE_DAYS:
        return None
    return payload


def _attach_cached_backtest_model(items: list[dict], model: dict) -> tuple[list[dict], dict]:
    grid = model.get("grid") or {}
    for item in items:
        score = min(MAX_SCORE, max(0.0, finite(item.get("score"), 0.0)))
        snapped = round(round(score / BACKTEST_MODEL_GRID_STEP) * BACKTEST_MODEL_GRID_STEP, 1)
        bt = dict(grid.get(f"{snapped:.1f}") or {})
        if bt:
            bt["current_score"] = round(score, 4)
            bt["model"] = "cached_full_category_pooled_nonoverlap_60d"
            bt["rank_influence"] = "none"
            bt["reference_generated_at_utc"] = model.get("generated_at_utc")
            bt["reference_reused_on_quick"] = True
        else:
            bt = {"available": False, "reason": "cached_backtest_grid_missing", "rank_influence": "none"}
        item["backtest"] = bt
    diagnostics = dict(model.get("diagnostics") or {})
    diagnostics["reference_reused_on_quick"] = True
    diagnostics["reference_generated_at_utc"] = model.get("generated_at_utc")
    return items, diagnostics

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
        return None, "listed_lt_400d"

    ind = add_indicators(frame)
    pos = len(frame) - 1
    close = finite(frame["Close"].iloc[pos])
    if not np.isfinite(close) or close < thresholds["min_price"]:
        return None, "price_lt_threshold"
    if not np.isfinite(finite(ind["PercentB"].iloc[pos])):
        return None, "indicator_history"

    size_info = resolve_market_size(stock, close, thresholds, size_cache)
    if stock.category in ETF_CATEGORIES:
        # ETFs remain exempt from the hard 10T universe rule, but their AUM/market
        # size is collected so the UI's 10/50/100/500/1000T filters work.
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

    profile_arrays = _profile_numpy(frame)
    score, scores, metrics = score_at(frame, ind, pos, profile_arrays=profile_arrays)
    # Forecast PJT 1 is intentionally isolated in trend_forecast.py. Existing
    # setup-score computation above is retained unchanged as a separate field.
    forecast_data = trend_forecast(frame)
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
        "base_score": round(score, 4),
        "score": round(score, 4),
        "display_score": round(score, 2),
        "forecast": forecast_data,
        "forecast_score": clean(forecast_data.get("score"), 8) if forecast_data.get("forecastable") else None,
        "scores": scores,
        "trade_signals": trade_signal_metrics(frame, ind, pos),
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


def _return_price_series(frame: pd.DataFrame) -> pd.Series:
    """Use dividend-adjusted closes when available; otherwise fall back to Close."""
    if "Adj Close" in frame.columns:
        adj = pd.to_numeric(frame["Adj Close"], errors="coerce")
        if adj.notna().sum() >= max(2, int(len(frame) * 0.8)):
            return adj
    return pd.to_numeric(frame["Close"], errors="coerce")


def historical_nonoverlap_samples(
    frame: pd.DataFrame,
    ticker: str,
    exclude_last: bool = False,
    category: str | None = None,
    current_market_size_krw: float = np.nan,
    current_close: float = np.nan,
) -> list[dict]:
    """Create non-overlapping 60-session historical score/return observations.

    Sampling backwards every 60 trading sessions avoids the old 59/60 overlap
    between neighboring forward-return labels. These observations are later
    pooled across the whole category rather than used as a noisy stock-specific
    expected-return estimate.
    """
    if frame is None or frame.empty:
        return []
    if exclude_last and len(frame) > 1:
        frame = frame.iloc[:-1]
    n = len(frame)
    min_pos = max(BB_WINDOW - 1, max(PROFILE_LOOKBACKS) - 1)
    last_i = n - BACKTEST_FORWARD_DAYS - 1
    if last_i < min_pos:
        return []

    ind = add_indicators(frame)
    profile_arrays = _profile_numpy(frame)
    ret_price = _return_price_series(frame)
    samples: list[dict] = []
    for i in range(last_i, min_pos - 1, -BACKTEST_NON_OVERLAP_STEP):
        hist_score, _, _ = score_at(frame, ind, i, profile_arrays=profile_arrays)
        hist_close = finite(frame["Close"].iloc[i])
        # Mitigate current-size look-ahead for equities. Exact point-in-time
        # shares are not available from the free source, so scale today's market
        # size by the historical/current split-adjusted price ratio as a proxy.
        if category not in ETF_CATEGORIES:
            size_now = finite(current_market_size_krw)
            close_now = finite(current_close)
            if np.isfinite(size_now) and size_now > 0 and np.isfinite(close_now) and close_now > 0 and np.isfinite(hist_close) and hist_close > 0:
                approx_hist_size = size_now * hist_close / close_now
                if approx_hist_size < MIN_MARKET_SIZE_KRW:
                    continue
        entry = finite(ret_price.iloc[i])
        exit_price = finite(ret_price.iloc[i + BACKTEST_FORWARD_DAYS])
        if not (np.isfinite(entry) and entry > 0 and np.isfinite(exit_price) and exit_price > 0):
            continue
        ret60 = exit_price / entry - 1.0
        samples.append({
            "ticker": ticker,
            "signal_date": pd.Timestamp(frame.index[i]).date().isoformat(),
            "score": round(hist_score, 4),
            "ret_60d": clean(ret60, 6),
        })
    return samples


def pooled_backtest_for_score(samples: list[dict], current_score: float) -> dict:
    """Return a pooled, non-overlapping 60D statistic for a current score band.

    Start with +/-0.5 score points. If the pool is thin, widen gradually up to
    +/-2.0. This value is informational only and is never used to rank stocks.
    """
    score = float(clip(finite(current_score, 0.0), 0.0, MAX_SCORE))
    valid = [
        s for s in samples
        if np.isfinite(finite(s.get("score"))) and np.isfinite(finite(s.get("ret_60d")))
    ]
    if not valid:
        return {
            "available": False,
            "reason": "no_pooled_samples",
            "current_score": round(score, 4),
            "signals": 0,
            "signals_used": 0,
            "avg_60d": None,
        }

    selected: list[dict] = []
    half_width = 0.5
    used_half_width = half_width
    while half_width <= BACKTEST_MAX_BAND_HALF_WIDTH + 1e-9:
        selected = [s for s in valid if abs(finite(s.get("score")) - score) <= half_width + 1e-12]
        used_half_width = half_width
        if len(selected) >= BACKTEST_TARGET_POOL_SAMPLES:
            break
        half_width += 0.5

    if not selected:
        return {
            "available": False,
            "reason": "no_score_band_samples",
            "current_score": round(score, 4),
            "signals": 0,
            "signals_used": 0,
            "avg_60d": None,
        }

    returns = np.array([finite(s["ret_60d"]) for s in selected], dtype=float)
    tickers = {str(s.get("ticker") or "") for s in selected}
    avg = float(np.mean(returns))
    med = float(np.median(returns))
    win = float(np.mean(returns > 0))
    std = float(np.std(returns, ddof=1)) if len(returns) >= 2 else np.nan
    recent = sorted(selected, key=lambda s: s.get("signal_date") or "", reverse=True)[:BACKTEST_RECENT_TRADES]
    return {
        "available": True,
        "model": "category_pooled_nonoverlap_60d_adjclose_v11_7",
        "current_score": round(score, 4),
        "score_band_half_width": clean(used_half_width, 2),
        "score_band_low": clean(max(0.0, score - used_half_width), 2),
        "score_band_high": clean(min(MAX_SCORE, score + used_half_width), 2),
        "signals": len(selected),
        "signals_used": len(selected),
        "stock_count": len(tickers),
        "avg_60d": clean(avg, 6),
        "median_60d": clean(med, 6),
        "win_60d": clean(win, 4),
        "std_60d": clean(std, 6),
        "forward_days": BACKTEST_FORWARD_DAYS,
        "sampling_step": BACKTEST_NON_OVERLAP_STEP,
        "return_basis": "adj_close_total_return_when_available",
        "rank_influence": "none",
        "trades": recent,
    }


def build_pooled_backtests(
    items: list[dict],
    frames: dict[str, pd.DataFrame],
    scan_mode: str = "FULL",
) -> tuple[list[dict], dict, dict]:
    """Attach statistically safer pooled backtests and return pool diagnostics."""
    samples: list[dict] = []
    for idx, item in enumerate(items, 1):
        frame = frames.get(item.get("ticker"))
        if frame is not None and not frame.empty:
            samples.extend(historical_nonoverlap_samples(
                frame,
                str(item.get("ticker") or ""),
                exclude_last=(scan_mode == "QUICK"),
                category=str(item.get("category") or ""),
                current_market_size_krw=finite(item.get("market_size_krw")),
                current_close=finite(item.get("close")),
            ))
        if idx % 100 == 0 or idx == len(items):
            print(f"[backtest-pool] histories {idx}/{len(items)} | samples={len(samples):,}")

    for item in items:
        item["backtest"] = pooled_backtest_for_score(samples, item.get("score", 0.0))

    bands = []
    for low in range(10):
        high = low + 1
        rows = [s for s in samples if low <= finite(s.get("score")) < high or (high == 10 and finite(s.get("score")) == 10)]
        if rows:
            rets = np.array([finite(s["ret_60d"]) for s in rows], dtype=float)
            bands.append({
                "score_low": low,
                "score_high": high,
                "samples": len(rows),
                "stocks": len({s.get("ticker") for s in rows}),
                "avg_60d": clean(np.mean(rets), 6),
                "median_60d": clean(np.median(rets), 6),
                "win_60d": clean(np.mean(rets > 0), 4),
            })
    diagnostics = {
        "sample_count": len(samples),
        "stock_count": len({s.get("ticker") for s in samples}),
        "sampling_step": BACKTEST_NON_OVERLAP_STEP,
        "forward_days": BACKTEST_FORWARD_DAYS,
        "score_bands": bands,
        "rank_influence": "none",
        "quick_active_bar_excluded": scan_mode == "QUICK",
        "historical_equity_size_filter": "approximate point-in-time market cap = current market cap * historical close/current close; ETFs exempt",
        "survivorship_note": "Current-universe constituents only; historical delisted constituents are unavailable from the free source. Backtest is reference-only and never ranks stocks.",
    }
    if len(bands) >= 2:
        x = np.array([(b["score_low"] + b["score_high"]) / 2.0 for b in bands], dtype=float)
        y = np.array([finite(b.get("avg_60d")) for b in bands], dtype=float)
        valid_xy = np.isfinite(x) & np.isfinite(y)
        if valid_xy.sum() >= 2:
            diagnostics["score_band_mean_return_corr"] = clean(np.corrcoef(x[valid_xy], y[valid_xy])[0, 1], 4)
            diffs = np.diff(y[valid_xy])
            diagnostics["monotonic_up_step_ratio"] = clean(np.mean(diffs >= 0), 4) if len(diffs) else None
    grid = {}
    grid_steps = int(round(MAX_SCORE / BACKTEST_MODEL_GRID_STEP))
    for idx in range(grid_steps + 1):
        grid_score = round(idx * BACKTEST_MODEL_GRID_STEP, 1)
        grid[f"{grid_score:.1f}"] = pooled_backtest_for_score(samples, grid_score)
    model = {
        "version": "DTC_BACKTEST_MODEL_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "grid_step": BACKTEST_MODEL_GRID_STEP,
        "forward_days": BACKTEST_FORWARD_DAYS,
        "grid": grid,
        "diagnostics": diagnostics,
    }
    return items, diagnostics, model


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
        "reason": (bt or {}).get("reason"),
        "signals": (bt or {}).get("signals"),
        "signals_used": (bt or {}).get("signals_used"),
        "stock_count": (bt or {}).get("stock_count"),
        "current_score": (bt or {}).get("current_score"),
        "score_band_half_width": (bt or {}).get("score_band_half_width"),
        "score_band_low": (bt or {}).get("score_band_low"),
        "score_band_high": (bt or {}).get("score_band_high"),
        "avg_60d": (bt or {}).get("avg_60d"),
        "median_60d": (bt or {}).get("median_60d"),
        "win_60d": (bt or {}).get("win_60d"),
        "std_60d": (bt or {}).get("std_60d"),
        "sampling_step": (bt or {}).get("sampling_step"),
        "return_basis": (bt or {}).get("return_basis"),
        "rank_influence": (bt or {}).get("rank_influence"),
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
        "base_score": item.get("base_score"),
        "score": item["score"],
        "display_score": item.get("display_score", item["score"]),
        "forecast": {k: v for k, v in (item.get("forecast") or {}).items() if k not in {"anchors", "projection", "fit_window"}},
        "forecast_score": item.get("forecast_score"),
        "scores": item.get("scores") or {},
        "trade_signals": item.get("trade_signals") or {},
        "sector": item.get("sector") or "—",
        "market_size_krw": item.get("market_size_krw"),
        "market_size_basis": item.get("market_size_basis"),
        "backtest": _compact_backtest(item.get("backtest") or {}),
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

    bundle_file = category_dir / "bundle.zip"
    # Build bundle from a temporary summary; publish summary last so clients
    # never receive references to detail files that are not yet complete.
    summary_tmp = category_dir / f".summary.build-{os.getpid()}.json"
    summary_tmp.write_text(json.dumps(summary_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    bundle_tmp = category_dir / f".bundle.build-{os.getpid()}.zip"
    with zipfile.ZipFile(bundle_tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(summary_tmp, "summary.json")
        zf.write(sizes_file, "sizes.json")
        backtest_model_file = category_dir / "backtest_model.json"
        if backtest_model_file.is_file():
            zf.write(backtest_model_file, "backtest_model.json")
        quarantine_file = category_dir / "yahoo-unavailable.json"
        if quarantine_file.is_file():
            zf.write(quarantine_file, "yahoo-unavailable.json")
        if universe_snapshot.is_file():
            zf.write(universe_snapshot, "universe.json")
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
    print(f"DTC v12.0 Forecast PJT 1 | {category} | mode={scan_mode} | universe={len(universe):,} | restricted={len(restricted):,}")
    print("profile score raw = BB 1 + current-zone concentration 9 + lower-support ratio 3 = 13; normalized to 10")
    print("candle score raw = weekly/monthly HA 1 + daily HA reversal 3 = 4; normalized to 10")
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

    # Forecast PJT 1 is the only scanner ranking model exposed in the UI.
    # Legacy setup scores remain in each item, but their historical backtests and
    # the removed Gwangju/Candle modes are no longer recomputed.
    unsorted_items = list(results.values())
    backtest_refreshed = False
    backtest_diagnostics = {
        "disabled": True,
        "reason": "legacy_setup_backtest_not_used_by_forecast_pjt_1",
        "forecast_backtest": "run backtest_forecast.py on 3y+ (recommended 5y+) historical OHLCV",
    }

    def _rank_key(item: dict):
        f = item.get("forecast") or {}
        if not f.get("forecastable"):
            return (1, 0.0, item.get("symbol", ""))
        return (0, -finite(f.get("score"), -1e18), item.get("symbol", ""))

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
        "strategy": "FORECAST_PJT_1_RELATIVE_VOLUME_ANCHOR_WLS",
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
        "max_score": MAX_SCORE,
        "forecast_model": {
            "name": "Forecast PJT 1",
            "window": 252,
            "relative_volume_sma": 20,
            "anchors": 6,
            "half_life": 84,
            "horizon": 20,
            "ranking": "forecast.score = clipped_20d_predicted_return * confidence",
            "minimum_history": 272,
            "price_basis": "Adj Close when available",
        },
        "score_model": {
            "score_max": MAX_SCORE,
            "bollinger": {
                "weight": BOLLINGER_MAX_SCORE,
                "formula": "1*(1-clamp(percentB,0,1))",
                "lower_band": 1,
                "middle_band": 0.5,
                "upper_band": 0,
                "window": BB_WINDOW,
                "sigma": BB_SIGMA,
            },
            "volume_profile": {
                "lookbacks": list(PROFILE_LOOKBACKS),
                "groups": {name: list(days) for name, days in PROFILE_GROUPS.items()},
                "group_weight": PROFILE_GROUP_WEIGHT,
                "lower_support_group_weight": LOWER_SUPPORT_GROUP_WEIGHT,
                "bins": PROFILE_BINS,
                "raw_share_formula": "volume_in_current_price_zone / total_volume_across_10_zones",
                "normalized_component": "current_zone_share / largest_zone_share",
                "group_formula": "3 * mean(normalized_component_of_group_lookbacks)",
                "lower_support_formula": "volume_in_zones_strictly_below_current_zone / total_volume_across_10_zones",
                "lower_support_group_formula": "1 * mean(lower_support_ratio_of_group_lookbacks)",
                "raw_score_max": PROFILE_RAW_MAX_SCORE,
                "final_normalization": "raw_score / 13 * 10",
                "allocation": "daily_volume_distributed_by_low_high_overlap",
            },
        },
        "forecast_backtest_model": {
            "script": "backtest_forecast.py",
            "sampling": "20-trading-day non-overlapping evaluation dates",
            "market_adjustment": "KR/US cross-sectional mean forward return removed at each date",
            "headline": "direction hit/base-rate/edge + date-level edge t-stat",
            "ranking_metric": "cross-sectional Spearman IC",
            "baselines": ["12-1 momentum", "3-month return", "no decay", "deterministic shuffled score"],
            "parameter_sweep": {"H": [42,63,84,105,126,"inf"], "N": [4,6,8], "h": [10,20,40]},
            "normal_scan_influence": "none; long-history research backtest is opt-in",
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
        help="FULL rebuilds the pooled 60D reference model + quiz; QUICK refreshes current scores with a short price window and reuses the latest FULL model/quiz.",
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
