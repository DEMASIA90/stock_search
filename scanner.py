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

MORNING_INVEST_COMPONENT_VERSION = "7.5"

import numpy as np
import pandas as pd
import yfinance as yf

from universe import Stock, fetch_kr_restricted_symbols, fetch_us_halted_symbols, get_universe

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ----------------------------
# Morning Invest strategy v8.5
# ----------------------------
# Fetch by explicit date range instead of relying on Yahoo's period parsing.
HISTORY_CALENDAR_DAYS = 1150
BATCH_SIZE = 24
RETRY_BATCH_SIZE = 4
DOWNLOAD_THREADS = 4
PRIMARY_BATCH_SLEEP = (0.55, 0.95)
RETRY_BATCH_SLEEP = (1.5, 2.8)
RETRY_ATTEMPTS = 3
CHART_POINTS = 120

# Never publish a materially incomplete market snapshot.
MIN_COVERAGE = {
    "KR": 0.95,
    "US": 0.95,
    "US_ETF": 0.95,
}

BB_WINDOW = 20
BB_SIGMA = 2.0
MIN_TRADING_DAYS = 250
MIN_PRICE_KRW = 1_000.0
SQUEEZE_BANDWIDTH = 0.08

# Score model.
SWING_LOOKBACK_DAYS = 200
SWING_FULL_SCORE_DAYS = 5  # ~1 trading week
SWING_MAX_SCORE = 0.5
SWING_MIN_SCORE = 0.1
DAILY_HA_MAX_AGE = 3
DAILY_HA_STREAK_CAP = 20
DAILY_HA_UNIT = 0.05  # 20 consecutive bearish HA days -> 1.0
WEEKLY_PSAR_MAX_SCORE = 0.50
WEEKLY_PSAR_DECAY_PER_WEEK = 0.10
WEEKLY_PSAR_ZERO_AGE = 5  # 5 weeks or more since bull flip => 0
MA60_SCORE = 0.50
RAW_MAX_SCORE = 3.5
DISPLAY_SCORE_MULTIPLIER = 100.0 / RAW_MAX_SCORE
DISPLAY_MAX_SCORE = 100.0

# Backtest: current strategy recreated point-in-time on each historical daily close.
# Signal is actionable only from the next trading day's open.
BACKTEST_LOOKBACK_DAYS = 252
BACKTEST_MIN_DISPLAY_SCORE = 70.0
BACKTEST_MIN_SCORE = RAW_MAX_SCORE * BACKTEST_MIN_DISPLAY_SCORE / 100.0  # 2.45 / 3.5
BACKTEST_COOLDOWN_DAYS = 10
BACKTEST_RECENT_TRADES = 12

# Backtest quality gate.
# Forecasts are produced only when all GOOD conditions are met.
BACKTEST_GOOD_MIN_SIGNALS = 5
BACKTEST_GOOD_MIN_WIN20 = 0.60
BACKTEST_GOOD_MIN_AVG20 = 0.02
BACKTEST_GOOD_MIN_MEDIAN20 = 0.00
BACKTEST_GOOD_MIN_AVG_MAE20 = -0.12
BACKTEST_GOOD_MIN_QUALITY = 60.0

BACKTEST_STRONG_MIN_SIGNALS = 8
BACKTEST_STRONG_MIN_WIN20 = 0.65
BACKTEST_STRONG_MIN_AVG20 = 0.04
BACKTEST_STRONG_MIN_QUALITY = 75.0

FORECAST_DAYS = 20
FORECAST_MAX_ANALOGS = 12
FORECAST_MIN_ANALOGS = 5

# Large-cap universe filter.
MIN_MARKET_SIZE_KRW = 10_000_000_000_000.0  # 10조원
STOCK_SHARES_CACHE_DAYS = 30
MARKET_SIZE_RETRY_ATTEMPTS = 3
MARKET_SIZE_MIN_LOOKUP_COVERAGE = 0.90

CATEGORY_DIR = {
    "KR": "kr",
    "US": "us",
    "US_ETF": "us-etf",
}
UNIVERSE_CACHE_FILE = {
    "KR": "universe_kr.json",
    "US": "universe_us.json",
    "US_ETF": "universe_us_etf.json",
}
CATEGORY_LABEL = {
    "KR": "국장",
    "US": "미장",
    "US_ETF": "미장 ETF",
}
CATEGORY_TZ = {
    "KR": "Asia/Seoul",
    "US": "America/New_York",
    "US_ETF": "America/New_York",
}
CATEGORY_CLOSE = {
    "KR": dtime(15, 40),
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
    """Normalize the OHLC fields used by the strategy."""
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
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    out = out[(out["Close"] > 0) & (out["High"] > 0) & (out["Low"] > 0)]
    return out



def completed_daily(frame: pd.DataFrame, category: str) -> pd.DataFrame:
    """Drop today's daily bar when the regular session is not yet safely closed."""
    if frame.empty:
        return frame
    now = datetime.now(ZoneInfo(CATEGORY_TZ[category]))
    out = frame
    if out.index[-1].date() == now.date() and now.time().replace(tzinfo=None) < CATEGORY_CLOSE[category]:
        out = out.iloc[:-1]
    return out


def add_daily_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["Close"]
    out["BB_Mid"] = close.rolling(BB_WINDOW).mean()
    std = close.rolling(BB_WINDOW).std(ddof=0)
    out["BB_Upper"] = out["BB_Mid"] + BB_SIGMA * std
    out["BB_Lower"] = out["BB_Mid"] - BB_SIGMA * std
    width = (out["BB_Upper"] - out["BB_Lower"]).replace(0, np.nan)
    out["PercentB"] = (close - out["BB_Lower"]) / width
    out["Bandwidth"] = width / out["BB_Mid"].replace(0, np.nan)
    out["MA60"] = close.rolling(60).mean()
    out["MA60_Slope"] = out["MA60"].diff()
    return out


def heikin_ashi(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(index=frame.index)
    o = frame["Open"].to_numpy(dtype=float)
    h = frame["High"].to_numpy(dtype=float)
    l = frame["Low"].to_numpy(dtype=float)
    c = frame["Close"].to_numpy(dtype=float)
    ha_close = (o + h + l + c) / 4.0
    ha_open = np.empty(len(frame), dtype=float)
    ha_open[0] = (o[0] + c[0]) / 2.0
    for i in range(1, len(frame)):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0
    ha_high = np.maximum.reduce([h, ha_open, ha_close])
    ha_low = np.minimum.reduce([l, ha_open, ha_close])
    return pd.DataFrame(
        {
            "HA_Open": ha_open,
            "HA_High": ha_high,
            "HA_Low": ha_low,
            "HA_Close": ha_close,
            "HA_Bull": ha_close > ha_open,
        },
        index=frame.index,
    )


def parabolic_sar(frame: pd.DataFrame, af_start=0.02, af_step=0.02, af_max=0.20) -> tuple[pd.Series, pd.Series]:
    """Classic Parabolic SAR and bull/bear state.

    bull=True means the SAR point is below the price trend; bull=False means above.
    """
    high = frame["High"].to_numpy(dtype=float)
    low = frame["Low"].to_numpy(dtype=float)
    n = len(frame)
    if n == 0:
        empty = pd.Series(dtype=float, index=frame.index)
        return empty, empty.astype(bool)

    sar = np.full(n, np.nan, dtype=float)
    bull_state = np.full(n, True, dtype=bool)

    bull = True
    if n >= 2:
        bull = frame["Close"].iloc[1] >= frame["Close"].iloc[0]
    af = af_start
    ep = high[0] if bull else low[0]
    sar[0] = low[0] if bull else high[0]
    bull_state[0] = bull

    for i in range(1, n):
        candidate = sar[i - 1] + af * (ep - sar[i - 1])

        if bull:
            candidate = min(candidate, low[i - 1])
            if i >= 2:
                candidate = min(candidate, low[i - 2])
            if low[i] < candidate:
                bull = False
                candidate = ep
                ep = low[i]
                af = af_start
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + af_step, af_max)
        else:
            candidate = max(candidate, high[i - 1])
            if i >= 2:
                candidate = max(candidate, high[i - 2])
            if high[i] > candidate:
                bull = True
                candidate = ep
                ep = high[i]
                af = af_start
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + af_step, af_max)

        sar[i] = candidate
        bull_state[i] = bull

    return pd.Series(sar, index=frame.index, name="PSAR"), pd.Series(bull_state, index=frame.index, name="PSAR_Bull")


def _resample_ohlc(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        frame.resample(rule)
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
        .dropna(subset=["Open", "High", "Low", "Close"])
    )



def active_weekly(frame: pd.DataFrame) -> pd.DataFrame:
    """Weekly OHLC including the current unfinished week from available daily bars."""
    return _resample_ohlc(frame, "W-FRI")



def ha_reversal_score(ha: pd.DataFrame, max_age: int, unit: float, streak_cap: int) -> tuple[float, int | None, int]:
    """Most recent bearish->bullish HA transition within max_age bars, including an active partial bar."""
    if len(ha) < 2:
        return 0.0, None, 0
    colors = ha["HA_Bull"].astype(bool).to_numpy()
    last = len(colors) - 1
    for transition in range(last, max(0, last - max_age) - 1, -1):
        if transition < 1:
            break
        if colors[transition] and not colors[transition - 1]:
            streak = 0
            j = transition - 1
            while j >= 0 and not colors[j]:
                streak += 1
                j -= 1
            age = last - transition
            return round(unit * min(streak, streak_cap), 6), age, streak
    return 0.0, None, 0


def score_percent_b(percent_b: float, bandwidth: float) -> tuple[float, bool]:
    """Lower-band proximity score for the complete searchable universe.

    %B<=0 receives the full 1.0. From lower to upper band the score decays
    smoothly to zero. %B>=1 receives zero rather than rising again.
    """
    if not np.isfinite(percent_b):
        score = 0.0
    elif percent_b <= 0:
        score = 1.0
    elif percent_b >= 1:
        score = 0.0
    else:
        score = (1.0 - percent_b) ** 2

    squeeze = bool(np.isfinite(bandwidth) and bandwidth < SQUEEZE_BANDWIDTH)
    if squeeze:
        score *= 0.5
    return round(score, 6), squeeze


def _swing_score_from_age(age: int | None) -> float:
    """Score a prior upper-band touch by trading-session age.

    1~5 sessions ago: full 0.5.
    6~200 sessions ago: linear decay from 0.5 to 0.1.
    Older/no touch: 0.
    """
    if age is None or age < 1 or age > SWING_LOOKBACK_DAYS:
        return 0.0
    if age <= SWING_FULL_SCORE_DAYS:
        return SWING_MAX_SCORE
    span = SWING_LOOKBACK_DAYS - SWING_FULL_SCORE_DAYS
    progress = (age - SWING_FULL_SCORE_DAYS) / span
    score = SWING_MAX_SCORE - (SWING_MAX_SCORE - SWING_MIN_SCORE) * progress
    return round(max(SWING_MIN_SCORE, min(SWING_MAX_SCORE, score)), 6)


def score_prior_upper_swing(percent_b: pd.Series) -> tuple[float, int | None]:
    # Use the most recent prior %B>=0.95 observation within 200 trading sessions.
    # Current bar is never counted as its own prior swing.
    values = pd.to_numeric(percent_b, errors="coerce").to_numpy(dtype=float)
    last = len(values) - 1
    for age in range(1, SWING_LOOKBACK_DAYS + 1):
        i = last - age
        if i < 0:
            break
        if np.isfinite(values[i]) and values[i] >= 0.95:
            return _swing_score_from_age(age), age
    return 0.0, None


def _weekly_psar_score_from_age(age: int | None) -> float:
    """Weekly PSAR bull-flip score.

    age=0: bear last week -> bull this week = 0.50
    age=1: 0.40
    age=2: 0.30
    age=3: 0.20
    age=4: 0.10
    age>=5 or no active bull state: 0
    """
    if age is None or age < 0 or age >= WEEKLY_PSAR_ZERO_AGE:
        return 0.0
    return round(max(0.0, WEEKLY_PSAR_MAX_SCORE - WEEKLY_PSAR_DECAY_PER_WEEK * age), 6)


def score_weekly_psar(frame: pd.DataFrame) -> tuple[float, bool, int | None, float]:
    """Weekly PSAR using the current unfinished week.

    A score exists only while the latest weekly PSAR is below price (bull state)
    and a bear->bull transition occurred within the last 4 weekly bars.
    """
    weekly = active_weekly(frame)
    if len(weekly) < 2:
        return 0.0, False, None, np.nan

    sar, bull = parabolic_sar(weekly, af_start=0.02, af_step=0.02, af_max=0.20)
    states = bull.to_numpy(dtype=bool)
    latest_below = bool(states[-1])
    if not latest_below:
        return 0.0, False, None, finite(sar.iloc[-1])

    transition = None
    for i in range(len(states) - 1, 0, -1):
        if bool(states[i]) and not bool(states[i - 1]):
            transition = i
            break

    age = None if transition is None else (len(states) - 1 - transition)
    return _weekly_psar_score_from_age(age), True, age, finite(sar.iloc[-1])



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
        print(f"USD/KRW fetch failed: {exc}")

    # Reuse the last successful FX written by a previous US/ETF scan; do not guess.
    for filename in ("us.json", "us_etf.json"):
        path = DATA_DIR / filename
        if path.exists():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                fx = finite(old.get("thresholds", {}).get("usdkrw"))
                if 500 <= fx <= 3000:
                    return fx
            except Exception:
                pass
    raise RuntimeError("USD/KRW unavailable and no previous valid FX snapshot exists")


def thresholds_for(category: str, usdkrw: float | None) -> dict:
    if category == "KR":
        return {
            "min_price": MIN_PRICE_KRW,
            "currency": "KRW",
            "usdkrw": None,
        }
    if not usdkrw:
        raise RuntimeError("USD/KRW is required for US thresholds")
    return {
        "min_price": MIN_PRICE_KRW / usdkrw,
        "currency": "USD",
        "usdkrw": round(usdkrw, 4),
    }


def _make_chart(ind: pd.DataFrame) -> dict:
    chart = ind.dropna(subset=["BB_Mid", "BB_Upper", "BB_Lower"]).tail(CHART_POINTS)
    return {
        "d": [pd.Timestamp(i).date().isoformat() for i in chart.index],
        "c": [clean(v) for v in chart["Close"]],
        "m": [clean(v) for v in chart["BB_Mid"]],
        "u": [clean(v) for v in chart["BB_Upper"]],
        "l": [clean(v) for v in chart["BB_Lower"]],
        "a60": [clean(v) for v in chart["MA60"]],
    }




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


def _cache_age_days(entry: dict) -> float:
    raw = entry.get("fetched_at")
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
    """Fetch slowly-changing equity market-size basis from Yahoo.

    ETFs are deliberately exempt from the KRW 10T market-size filter and must
    never trigger per-ticker AUM/market-cap lookups here.
    """
    if stock.category == "US_ETF":
        return None

    ticker = yf.Ticker(stock.ticker)

    # Equity: cache share count and recompute market cap from today's close.
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

    # Fallback to direct market cap if Yahoo cannot provide shares.
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


def resolve_market_size(
    stock: Stock,
    close: float,
    thresholds: dict,
    size_cache: dict,
) -> tuple[float, float, str] | None:
    """Return (native_size, KRW_size, basis) for equities.

    US ETFs are exempt and return an explicit non-network sentinel.
    """
    if stock.category == "US_ETF":
        return np.nan, np.nan, "exempt"

    entry = size_cache.get(stock.ticker)
    max_age = STOCK_SHARES_CACHE_DAYS

    if not isinstance(entry, dict) or _cache_age_days(entry) > max_age:
        entry = None
        for attempt in range(1, MARKET_SIZE_RETRY_ATTEMPTS + 1):
            try:
                entry = _fetch_stock_size_basis(stock)
                if entry:
                    size_cache[stock.ticker] = entry
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

    if basis == "shares":
        native_size = value * close
    elif basis in {"market_cap", "total_assets"}:
        native_size = value
    else:
        return None

    usdkrw = finite(thresholds.get("usdkrw"))
    if stock.currency == "KRW":
        size_krw = native_size
    else:
        if not np.isfinite(usdkrw) or usdkrw <= 0:
            return None
        size_krw = native_size * usdkrw

    return float(native_size), float(size_krw), basis

def analyze(
    stock: Stock,
    raw_frame: pd.DataFrame,
    thresholds: dict,
    restricted_symbols: set[str],
    size_cache: dict,
) -> tuple[dict | None, str]:
    if stock.symbol in restricted_symbols or stock.ticker in restricted_symbols:
        return None, "restricted_status"

    frame = completed_daily(_numeric_ohlc(raw_frame), stock.category)
    if frame.empty:
        return None, "no_price"
    if len(frame) < MIN_TRADING_DAYS:
        return None, "listed_lt_250d"

    ind = add_daily_indicators(frame)
    valid = ind.dropna(subset=["BB_Mid", "BB_Upper", "BB_Lower", "PercentB"])
    if len(valid) < 120:
        return None, "indicator_history"

    row = valid.iloc[-1]
    close = finite(row["Close"])
    percent_b = finite(row["PercentB"])
    bandwidth = finite(row["Bandwidth"])
    if close < thresholds["min_price"]:
        return None, "price_lt_threshold"
    if not np.isfinite(percent_b):
        return None, "percent_b_unavailable"

    # 0. Large-cap filter: equities must be >= KRW 10T.
    # US ETFs are fully exempt: no AUM/market-cap request and all pass this step.
    if stock.category == "US_ETF":
        market_size_native, market_size_krw, market_size_basis = np.nan, np.nan, "exempt"
    else:
        size_info = resolve_market_size(stock, close, thresholds, size_cache)
        if size_info is None:
            return None, "market_size_unavailable"
        market_size_native, market_size_krw, market_size_basis = size_info
        if market_size_krw < MIN_MARKET_SIZE_KRW:
            return None, "market_size_lt_10t"

    # 1. Daily %B score, with squeeze penalty.
    s1, squeeze = score_percent_b(percent_b, bandwidth)

    # 2. Previous upper-band swing within 200 trading sessions; recent touches score more.
    s2, upper_swing_age = score_prior_upper_swing(ind["PercentB"])

    # 3. Weekly PSAR, including the current unfinished week.
    # Score only after an above->below (bear->bull) flip:
    # current week 0.50, then 0.40/0.30/0.20/0.10, >=5 weeks 0.
    s3, weekly_psar_below, weekly_psar_flip_age, weekly_psar_value = score_weekly_psar(frame)

    # 4. Daily HA reversal only. 20 consecutive bearish HA days before the
    # bearish->bullish transition receive the full 1.0 point. The existing
    # freshness window is retained: the transition remains active for 3 sessions.
    daily_ha = heikin_ashi(frame)
    s4, d_ha_age, d_ha_bear_streak = ha_reversal_score(
        daily_ha,
        max_age=DAILY_HA_MAX_AGE,
        unit=DAILY_HA_UNIT,
        streak_cap=DAILY_HA_STREAK_CAP,
    )

    # 5. 60-day moving-average slope. Positive one-session slope = 0.50.
    ma60 = finite(ind["MA60"].iloc[-1])
    ma60_prev = finite(ind["MA60"].iloc[-2]) if len(ind) >= 2 else np.nan
    ma60_slope = ma60 - ma60_prev if np.isfinite(ma60) and np.isfinite(ma60_prev) else np.nan
    ma60_slope_pct = (ma60 / ma60_prev - 1.0) if np.isfinite(ma60) and np.isfinite(ma60_prev) and ma60_prev != 0 else np.nan
    s5 = MA60_SCORE if np.isfinite(ma60_slope) and ma60_slope > 0 else 0.0

    total = round(s1 + s2 + s3 + s4 + s5, 4)

    last_date = pd.Timestamp(row.name).date().isoformat()
    prev_close = finite(valid["Close"].iloc[-2]) if len(valid) >= 2 else np.nan
    day_change = (close / prev_close - 1.0) * 100 if prev_close > 0 else np.nan

    item = {
        "ticker": stock.ticker,
        "symbol": stock.symbol,
        "name": stock.name,
        "category": stock.category,
        "exchange": stock.exchange,
        "currency": stock.currency,
        "date": last_date,
        "close": clean(close),
        "day_change_pct": clean(day_change, 2),
        "score": total,
        "display_score": round(total * DISPLAY_SCORE_MULTIPLIER, 1),
        "scores": {
            "s1_percent_b": round(s1, 4),
            "s2_upper_swing": round(s2, 4),
            "s3_weekly_psar": round(s3, 4),
            "s4_daily_ha": round(s4, 4),
            "s5_ma60_slope": round(s5, 4),
        },
        "metrics": {
            "percent_b": clean(percent_b, 4),
            "bandwidth": clean(bandwidth, 4),
            "squeeze": squeeze,
            "bb_lower": clean(row["BB_Lower"]),
            "bb_mid": clean(row["BB_Mid"]),
            "bb_upper": clean(row["BB_Upper"]),
            "upper_swing_age": upper_swing_age,
            "weekly_psar_below": weekly_psar_below,
            "weekly_psar_flip_age": weekly_psar_flip_age,
            "weekly_psar_value": clean(weekly_psar_value),
            "daily_ha_age": d_ha_age,
            "daily_ha_prior_bear": d_ha_bear_streak,
            "ma60": clean(ma60),
            "ma60_slope": clean(ma60_slope, 6),
            "ma60_slope_pct": clean(ma60_slope_pct, 6),
            "market_size_native": clean(market_size_native, 0),
            "market_size_krw": clean(market_size_krw, 0),
            "market_size_basis": market_size_basis,
        },
        "chart": _make_chart(ind),
        "backtest": backtest_stock(frame, stock.category, thresholds, total),
    }
    return item, "passed"



def _ha_reversal_score_series(
    ha: pd.DataFrame,
    max_age: int,
    unit: float,
    streak_cap: int,
) -> pd.DataFrame:
    """Historical HA reversal score without look-ahead."""
    result = pd.DataFrame(
        {"score": 0.0, "age": np.nan, "streak": 0},
        index=ha.index,
    )
    if len(ha) < 2:
        return result

    colors = ha["HA_Bull"].astype(bool).to_numpy()
    score_col = result.columns.get_loc("score")
    age_col = result.columns.get_loc("age")
    streak_col = result.columns.get_loc("streak")

    for transition in range(1, len(colors)):
        if not (colors[transition] and not colors[transition - 1]):
            continue
        streak = 0
        j = transition - 1
        while j >= 0 and not colors[j]:
            streak += 1
            j -= 1
        score = unit * min(streak, streak_cap)
        for age in range(max_age + 1):
            pos = transition + age
            if pos >= len(result):
                break
            result.iat[pos, score_col] = score
            result.iat[pos, age_col] = age
            result.iat[pos, streak_col] = streak
    return result


def _psar_step(state: dict, high: float, low: float) -> dict:
    """Advance a seeded PSAR state by one OHLC bar."""
    bull = bool(state["bull"])
    sar_prev = float(state["sar"])
    ep = float(state["ep"])
    af = float(state["af"])

    candidate = sar_prev + af * (ep - sar_prev)

    if bull:
        candidate = min(candidate, float(state["prev_low_1"]))
        if state["count"] >= 2:
            candidate = min(candidate, float(state["prev_low_2"]))
        if low < candidate:
            bull = False
            candidate = ep
            ep = low
            af = 0.02
        elif high > ep:
            ep = high
            af = min(af + 0.02, 0.20)
    else:
        candidate = max(candidate, float(state["prev_high_1"]))
        if state["count"] >= 2:
            candidate = max(candidate, float(state["prev_high_2"]))
        if high > candidate:
            bull = True
            candidate = ep
            ep = high
            af = 0.02
        elif low < ep:
            ep = low
            af = min(af + 0.02, 0.20)

    return {
        "sar": candidate,
        "bull": bull,
        "ep": ep,
        "af": af,
        "prev_high_2": state["prev_high_1"],
        "prev_low_2": state["prev_low_1"],
        "prev_high_1": high,
        "prev_low_1": low,
        "count": state["count"] + 1,
        "last_transition_idx": (
            state["count"] if (bull and not bool(state["bull"]))
            else state.get("last_transition_idx")
        ),
    }


def _psar_seed(first: dict, second: dict) -> dict:
    """Create the same initial state used by parabolic_sar() after bar #2."""
    bull = float(second["Close"]) >= float(first["Close"])
    state = {
        "sar": float(first["Low"]) if bull else float(first["High"]),
        "bull": bull,
        "ep": float(first["High"]) if bull else float(first["Low"]),
        "af": 0.02,
        "prev_high_2": float(first["High"]),
        "prev_low_2": float(first["Low"]),
        "prev_high_1": float(first["High"]),
        "prev_low_1": float(first["Low"]),
        "count": 1,
        "last_transition_idx": None,
    }
    seeded = _psar_step(state, float(second["High"]), float(second["Low"]))
    # Initial direction is not treated as a bear->bull "flip". Only a real
    # transition after the initialized state earns the weekly PSAR score.
    seeded["last_transition_idx"] = None
    return seeded


def _weekly_psar_score_series(frame: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time weekly PSAR score on every daily close without look-ahead.

    Each daily observation builds the current unfinished weekly OHLC only from
    data available through that date. Completed weeks are committed once.
    """
    result = pd.DataFrame(
        {"score": 0.0, "below": False, "flip_age": np.nan},
        index=frame.index,
    )
    if frame.empty:
        return result

    # Friday-ending weekly periods; an in-progress week is represented by its
    # available daily bars only.
    periods = frame.index.to_period("W-FRI")
    completed_bars: list[dict] = []
    base_state: dict | None = None
    week_index = -1

    for _, group in frame.groupby(periods, sort=True):
        week_index += 1
        week_open = finite(group["Open"].iloc[0])
        running_high = -np.inf
        running_low = np.inf
        final_bar = None

        for idx, row in group.iterrows():
            running_high = max(running_high, finite(row["High"]))
            running_low = min(running_low, finite(row["Low"]))
            current_bar = {
                "Open": week_open,
                "High": running_high,
                "Low": running_low,
                "Close": finite(row["Close"]),
            }

            simulated = None
            if len(completed_bars) == 0:
                simulated = None
            elif len(completed_bars) == 1 and base_state is None:
                simulated = _psar_seed(completed_bars[0], current_bar)
            elif base_state is not None:
                simulated = _psar_step(base_state, current_bar["High"], current_bar["Low"])

            if simulated is not None:
                below = bool(simulated["bull"])
                flip_age = None
                if below:
                    transition_idx = simulated.get("last_transition_idx")
                    if transition_idx is not None:
                        flip_age = week_index - int(transition_idx)

                result.at[idx, "below"] = below
                result.at[idx, "flip_age"] = np.nan if flip_age is None else flip_age
                result.at[idx, "score"] = _weekly_psar_score_from_age(flip_age) if below else 0.0

            final_bar = current_bar

        if final_bar is None:
            continue

        if len(completed_bars) == 0:
            completed_bars.append(final_bar)
        elif len(completed_bars) == 1 and base_state is None:
            base_state = _psar_seed(completed_bars[0], final_bar)
            completed_bars.append(final_bar)
        else:
            base_state = _psar_step(base_state, final_bar["High"], final_bar["Low"])
            completed_bars.append(final_bar)

    return result


def build_historical_scores(
    frame: pd.DataFrame,
    category: str,
    thresholds: dict,
) -> pd.DataFrame:
    """Recreate the strategy at every historical close with no future data."""
    ind = add_daily_indicators(frame)
    hist = pd.DataFrame(index=ind.index)

    # 1) %B score + squeeze penalty
    pb = pd.to_numeric(ind["PercentB"], errors="coerce")
    bw = pd.to_numeric(ind["Bandwidth"], errors="coerce")
    s1 = ((1.0 - pb) ** 2).clip(upper=1.0)
    s1 = s1.where(pb > 0, 1.0)
    s1 = s1.where(~(bw < SQUEEZE_BANDWIDTH), s1 * 0.5)
    hist["s1"] = s1

    # 2) most-recent prior upper-band observation within 200 sessions.
    # Point-in-time reconstruction: today's %B is only eligible for future days.
    s2_values = np.zeros(len(ind), dtype=float)
    last_upper_pos: int | None = None
    pb_values = pb.to_numpy(dtype=float)
    for i, value in enumerate(pb_values):
        if last_upper_pos is not None:
            age = i - last_upper_pos
            if age <= SWING_LOOKBACK_DAYS:
                s2_values[i] = _swing_score_from_age(age)
        if np.isfinite(value) and value >= 0.95:
            last_upper_pos = i
    hist["s2"] = pd.Series(s2_values, index=ind.index)

    # 3) Weekly PSAR bear->bull flip age, including the active unfinished week.
    psar_hist = _weekly_psar_score_series(frame)
    hist["s3"] = pd.to_numeric(psar_hist["score"], errors="coerce").fillna(0.0)

    # 4) Daily HA reversal only. 20 bearish days before reversal = 1.0.
    daily_ha = heikin_ashi(frame)
    d_hist = _ha_reversal_score_series(
        daily_ha,
        max_age=DAILY_HA_MAX_AGE,
        unit=DAILY_HA_UNIT,
        streak_cap=DAILY_HA_STREAK_CAP,
    )
    hist["s4"] = d_hist["score"]

    # 5) Positive 60-day MA slope.
    ma60_slope = pd.to_numeric(ind["MA60_Slope"], errors="coerce")
    hist["s5"] = (ma60_slope > 0).astype(float) * MA60_SCORE

    # Total raw score max = 3.5: 1.0 + 0.5 + 0.5 + 1.0 + 0.5.
    hist["score"] = hist["s1"] + hist["s2"] + hist["s3"] + hist["s4"] + hist["s5"]

    # 0-step point-in-time market-data filters.
    enough_history = pd.Series(np.arange(len(ind)) >= (MIN_TRADING_DAYS - 1), index=ind.index)
    hist["eligible"] = (
        enough_history
        & (ind["Close"] >= thresholds["min_price"])
        & hist["score"].notna()
    )
    return hist


def _mean(values):
    vals = [finite(v) for v in values]
    vals = [v for v in vals if np.isfinite(v)]
    return float(np.mean(vals)) if vals else np.nan


def _median(values):
    vals = [finite(v) for v in values]
    vals = [v for v in vals if np.isfinite(v)]
    return float(np.median(vals)) if vals else np.nan


def _win_rate(values):
    vals = [finite(v) for v in values]
    vals = [v for v in vals if np.isfinite(v)]
    return float(np.mean([v > 0 for v in vals])) if vals else np.nan



def _quality_score(signals: int, avg20: float, median20: float, win20: float, avg_mae20: float) -> float:
    """0~100 robustness score. This does not affect the stock ranking."""
    sample_part = 25.0 * clip(signals / 10.0, 0.0, 1.0)
    win_part = 30.0 * clip((finite(win20, 0.0) - 0.45) / 0.30, 0.0, 1.0)
    avg_part = 25.0 * clip(finite(avg20, 0.0) / 0.10, 0.0, 1.0)
    median_part = 10.0 * clip(finite(median20, 0.0) / 0.06, 0.0, 1.0)
    # Average MAE: -15% -> 0 points, 0% -> 10 points.
    mae_part = 10.0 * clip((finite(avg_mae20, -0.15) + 0.15) / 0.15, 0.0, 1.0)
    return round(sample_part + win_part + avg_part + median_part + mae_part, 1)


def _quality_label(signals: int, avg20: float, median20: float, win20: float, avg_mae20: float, quality: float) -> str:
    good = (
        signals >= BACKTEST_GOOD_MIN_SIGNALS
        and finite(win20, 0.0) >= BACKTEST_GOOD_MIN_WIN20
        and finite(avg20, -1.0) >= BACKTEST_GOOD_MIN_AVG20
        and finite(median20, -1.0) > BACKTEST_GOOD_MIN_MEDIAN20
        and finite(avg_mae20, -1.0) >= BACKTEST_GOOD_MIN_AVG_MAE20
        and quality >= BACKTEST_GOOD_MIN_QUALITY
    )
    if not good:
        return "NORMAL"

    strong = (
        signals >= BACKTEST_STRONG_MIN_SIGNALS
        and finite(win20, 0.0) >= BACKTEST_STRONG_MIN_WIN20
        and finite(avg20, -1.0) >= BACKTEST_STRONG_MIN_AVG20
        and quality >= BACKTEST_STRONG_MIN_QUALITY
    )
    return "STRONG" if strong else "GOOD"


def _build_forecast(trades: list[dict], current_score: float, current_price: float, quality_label: str) -> dict:
    """Build a 20-session empirical analog forecast.

    Historical events closest to the current strategy score receive higher weight.
    Output is a scenario projection, not a probability-calibrated price target.
    """
    if quality_label not in {"GOOD", "STRONG"}:
        return {"available": False, "reason": "quality_gate"}

    valid = [
        t for t in trades
        if len(t.get("_path20") or []) >= FORECAST_DAYS
        and t.get("score") is not None
    ]
    if len(valid) < FORECAST_MIN_ANALOGS:
        return {"available": False, "reason": "insufficient_analogs"}

    valid.sort(key=lambda t: abs(finite(t["score"]) - finite(current_score)))
    analogs = valid[:FORECAST_MAX_ANALOGS]

    paths = np.array(
        [[finite(v) for v in t["_path20"][:FORECAST_DAYS]] for t in analogs],
        dtype=float,
    )
    if paths.ndim != 2 or paths.shape[0] < FORECAST_MIN_ANALOGS:
        return {"available": False, "reason": "insufficient_analogs"}

    # Similarity weight: a 0.35 score gap receives ~37% of the weight of an exact match.
    gaps = np.array([abs(finite(t["score"]) - finite(current_score)) for t in analogs], dtype=float)
    weights = np.exp(-gaps / 0.35)
    weights = weights / weights.sum() if weights.sum() > 0 else np.ones(len(analogs)) / len(analogs)

    mean_path = np.average(paths, axis=0, weights=weights)
    q25 = np.nanquantile(paths, 0.25, axis=0)
    q75 = np.nanquantile(paths, 0.75, axis=0)

    current_price = finite(current_price)
    if not np.isfinite(current_price) or current_price <= 0:
        return {"available": False, "reason": "invalid_current_price"}

    mean_prices = current_price * (1.0 + mean_path)
    low_prices = current_price * (1.0 + q25)
    high_prices = current_price * (1.0 + q75)

    return {
        "available": True,
        "method": "same_stock_historical_signal_analogs",
        "days": list(range(1, FORECAST_DAYS + 1)),
        "sample_count": len(analogs),
        "current_price": clean(current_price),
        "expected_return_20d": clean(mean_path[-1], 5),
        "expected_price_20d": clean(mean_prices[-1]),
        "range_low_20d": clean(low_prices[-1]),
        "range_high_20d": clean(high_prices[-1]),
        "mean_price": [clean(v) for v in mean_prices],
        "low_price": [clean(v) for v in low_prices],
        "high_price": [clean(v) for v in high_prices],
    }

def backtest_stock(
    frame: pd.DataFrame,
    category: str,
    thresholds: dict,
    current_score: float,
) -> dict:
    """Single-stock event backtest for the current Morning Invest strategy.

    - Signal is calculated using data available at that day's close.
    - Entry is the next trading day's open.
    - 5/10/20D outcomes use subsequent closes.
    - A 10-trading-day cooldown prevents repeated counting of the same setup.
    - Historical regulatory/watch-list status is not reconstructed here.
    """
    if len(frame) < MIN_TRADING_DAYS + 25:
        return {
            "available": False,
            "reason": "insufficient_history",
            "signals": 0,
            "trades": [],
        }

    hist = build_historical_scores(frame, category, thresholds)
    n = len(frame)
    # Search at most the most recent ~1 trading year. The last 20 sessions are
    # excluded because a complete 20D outcome is not known yet.
    start = max(MIN_TRADING_DAYS - 1, n - BACKTEST_LOOKBACK_DAYS)
    end = n - 21

    trades = []
    last_signal = -10_000
    for i in range(start, max(start, end)):
        if not bool(hist["eligible"].iloc[i]):
            continue
        # Backtest only historical Morning Invest signals scoring >=70/100 (raw >=2.45).
        if finite(hist["score"].iloc[i], -np.inf) < BACKTEST_MIN_SCORE:
            continue
        if i - last_signal < BACKTEST_COOLDOWN_DAYS:
            continue

        entry_i = i + 1
        entry = finite(frame["Open"].iloc[entry_i])
        if not np.isfinite(entry) or entry <= 0:
            continue

        ret5 = finite(frame["Close"].iloc[i + 5] / entry - 1.0)
        ret10 = finite(frame["Close"].iloc[i + 10] / entry - 1.0)
        ret20 = finite(frame["Close"].iloc[i + 20] / entry - 1.0)

        future = frame.iloc[entry_i : i + 21]
        mfe20 = finite(future["High"].max() / entry - 1.0)
        mae20 = finite(future["Low"].min() / entry - 1.0)

        # D+1..D+20 close-return path relative to the next-session open.
        path20 = []
        for step in range(20):
            pos = entry_i + step
            if pos >= len(frame):
                break
            path20.append(clean(frame["Close"].iloc[pos] / entry - 1.0, 6))

        trades.append(
            {
                "signal_date": pd.Timestamp(frame.index[i]).date().isoformat(),
                "entry_date": pd.Timestamp(frame.index[entry_i]).date().isoformat(),
                "score": clean(hist["score"].iloc[i], 3),
                "entry": clean(entry),
                "ret_5d": clean(ret5, 5),
                "ret_10d": clean(ret10, 5),
                "ret_20d": clean(ret20, 5),
                "mfe_20d": clean(mfe20, 5),
                "mae_20d": clean(mae20, 5),
                "_path20": path20,
            }
        )
        last_signal = i

    r5 = [t["ret_5d"] for t in trades if t["ret_5d"] is not None]
    r10 = [t["ret_10d"] for t in trades if t["ret_10d"] is not None]
    r20 = [t["ret_20d"] for t in trades if t["ret_20d"] is not None]
    mfe = [t["mfe_20d"] for t in trades if t["mfe_20d"] is not None]
    mae = [t["mae_20d"] for t in trades if t["mae_20d"] is not None]
    scores = [t["score"] for t in trades if t["score"] is not None]

    avg20 = _mean(r20)
    median20 = _median(r20)
    win20 = _win_rate(r20)
    avg_mae20 = _mean(mae)
    quality = _quality_score(len(trades), avg20, median20, win20, avg_mae20)
    quality_label = _quality_label(len(trades), avg20, median20, win20, avg_mae20, quality)
    current_price = finite(frame["Close"].iloc[-1])
    forecast = _build_forecast(trades, current_score, current_price, quality_label)

    public_trades = []
    for trade in trades[-BACKTEST_RECENT_TRADES:][::-1]:
        public_trades.append({k: v for k, v in trade.items() if not k.startswith("_")})

    return {
        "available": True,
        "lookback_days": BACKTEST_LOOKBACK_DAYS,
        "min_signal_score": BACKTEST_MIN_SCORE,
        "min_signal_display_score": BACKTEST_MIN_DISPLAY_SCORE,
        "eval_days": max(0, end - start),
        "cooldown_days": BACKTEST_COOLDOWN_DAYS,
        "entry_rule": "next_open",
        "signals": len(trades),
        "avg_score": clean(_mean(scores), 3),
        "avg_5d": clean(_mean(r5), 5),
        "win_5d": clean(_win_rate(r5), 4),
        "avg_10d": clean(_mean(r10), 5),
        "win_10d": clean(_win_rate(r10), 4),
        "avg_20d": clean(avg20, 5),
        "median_20d": clean(median20, 5),
        "win_20d": clean(win20, 4),
        "best_20d": clean(max(r20), 5) if r20 else None,
        "worst_20d": clean(min(r20), 5) if r20 else None,
        "avg_mfe_20d": clean(_mean(mfe), 5),
        "avg_mae_20d": clean(avg_mae20, 5),
        "quality_score": quality,
        "quality_label": quality_label,
        "forecast": forecast,
        "trades": public_trades,
        "limitations": [
            "historical_regulatory_status_not_reconstructed",
            "historical_market_cap_not_reconstructed"
        ],
    }


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


def download_batch(tickers: list[str], timeout=40) -> pd.DataFrame:
    end = datetime.now(timezone.utc).date() + timedelta(days=1)
    start = end - timedelta(days=HISTORY_CALENDAR_DAYS)
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
    halted, meta = fetch_us_halted_symbols()
    return halted, meta



def _detail_filename(item: dict) -> str:
    symbol = re.sub(r"[^A-Za-z0-9._-]+", "_", str(item.get("symbol") or "stock")).strip("._-")
    symbol = (symbol or "stock")[:36]
    digest = hashlib.sha1(str(item["ticker"]).encode("utf-8")).hexdigest()[:12]
    return f"{symbol}-{digest}.json"


def _summary_item(item: dict, detail_path: str) -> dict:
    """Small row payload used by the initial market screen."""
    bt = item.get("backtest") or {}
    forecast = bt.get("forecast") or {}
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
        "display_score": item.get("display_score", round(float(item["score"]) * DISPLAY_SCORE_MULTIPLIER, 1)),
        "scores": item["scores"],
        "market_size_krw": (item.get("metrics") or {}).get("market_size_krw"),
        "market_size_basis": (item.get("metrics") or {}).get("market_size_basis"),
        "backtest": {
            "available": bool(bt.get("available")),
            "signals": bt.get("signals"),
            "avg_20d": bt.get("avg_20d"),
            "win_20d": bt.get("win_20d"),
            "quality_score": bt.get("quality_score"),
            "quality_label": bt.get("quality_label", "NORMAL"),
            "forecast_available": bool(forecast.get("available")),
        },
        "detail_path": detail_path,
    }


def _write_category_site(category: str, payload_meta: dict, items: list[dict], size_cache: dict) -> tuple[Path, int]:
    """Write a lightweight summary plus one detail JSON per passing symbol.

    The generated directory is a deployment artifact only. It is intentionally
    not stored in Git history.
    """
    category_dir = DATA_DIR / CATEGORY_DIR[category]
    shutil.rmtree(category_dir, ignore_errors=True)
    stocks_dir = category_dir / "stocks"
    stocks_dir.mkdir(parents=True, exist_ok=True)

    summary_items = []
    for item in items:
        filename = _detail_filename(item)
        relative_detail = f"data/{CATEGORY_DIR[category]}/stocks/{filename}"
        summary_items.append(_summary_item(item, relative_detail))

        detail_file = stocks_dir / filename
        detail_file.write_text(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    summary_payload = {
        **payload_meta,
        "storage_model": "summary_plus_lazy_stock_detail_v7",
        "detail_count": len(items),
        "items": summary_items,
    }
    summary_file = category_dir / "summary.json"
    summary_file.write_text(
        json.dumps(summary_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # Persist slow-changing equity share/market-cap cache inside the Hosting snapshot.
    sizes_file = category_dir / "sizes.json"
    sizes_file.write_text(
        json.dumps(size_cache, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # Keep a copy of the last valid listing universe inside the Hosting-only
    # category snapshot. hydrate_data.py restores it to the root cache before
    # the next scan, allowing get_universe() to fall back during source outages.
    universe_snapshot = category_dir / "universe.json"
    root_universe_cache = DATA_DIR / UNIVERSE_CACHE_FILE[category]
    if root_universe_cache.is_file():
        shutil.copy2(root_universe_cache, universe_snapshot)

    # A compressed category snapshot is retained on Hosting only so a later
    # partial-market workflow can restore categories without Git.
    bundle_file = category_dir / "bundle.zip"
    with zipfile.ZipFile(bundle_file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(summary_file, "summary.json")
        if universe_snapshot.is_file():
            zf.write(universe_snapshot, "universe.json")
        if sizes_file.is_file():
            zf.write(sizes_file, "sizes.json")
        for detail_file in sorted(stocks_dir.glob("*.json")):
            zf.write(detail_file, f"stocks/{detail_file.name}")

    return category_dir, len(items)

def scan_category(category: str, usdkrw: float | None = None) -> None:
    universe, universe_source = get_universe(category)
    thresholds = thresholds_for(category, usdkrw)
    restricted, restriction_meta = _load_restrictions(category, universe)
    size_cache = _load_size_cache(category)

    print("=" * 72)
    print(f"Morning Invest | {category} | universe={len(universe):,} | restricted={len(restricted):,}")
    size_rule = (
        "market size filter=OFF (ETF exempt)"
        if category == "US_ETF"
        else f"market size>=KRW {MIN_MARKET_SIZE_KRW/1e12:.0f}T"
    )
    print(
        f"thresholds: close>={thresholds['min_price']:.4f} {thresholds['currency']}, "
        f"{size_rule}"
    )
    print("=" * 72)

    results: dict[str, dict] = {}
    rejection = Counter()
    priced_tickers: set[str] = set()
    missing: list[str] = []
    by_ticker = {s.ticker: s for s in universe}

    # Step-0 restricted symbols are excluded before requesting Yahoo prices.
    # This both follows the strategy definition and avoids wasting requests on
    # halted/watch-list symbols that often have no current Yahoo history.
    scan_universe = [s for s in universe if s.ticker not in restricted]
    rejection["restricted_status"] += len(universe) - len(scan_universe)

    print(
        f"[{category}] price-download universe={len(scan_universe):,} "
        f"(step-0 restricted skipped={len(universe)-len(scan_universe):,})"
    )

    batches = list(chunks(scan_universe, BATCH_SIZE))
    total_batches = len(batches)
    for batch_no, batch in enumerate(batches, 1):
        tickers = [s.ticker for s in batch]
        try:
            raw = download_batch(tickers)
        except Exception as exc:
            print(f"[{category}] batch {batch_no}/{total_batches} failed: {exc}")
            missing.extend(tickers)
            time.sleep(1.5)
            continue

        for stock in batch:
            try:
                frame = frame_for(raw, stock.ticker)
                if frame is not None and not frame.empty:
                    priced_tickers.add(stock.ticker)
                item, reason = analyze(stock, frame, thresholds, restricted, size_cache)
                rejection[reason] += 1
                if item is not None:
                    results[stock.ticker] = item
                elif reason in {"no_price", "indicator_history"}:
                    missing.append(stock.ticker)
            except Exception as exc:
                rejection["analysis_error"] += 1
                missing.append(stock.ticker)
                print(f"[{category}] {stock.ticker} analyze error: {type(exc).__name__}: {exc}")

        if batch_no % 10 == 0 or batch_no == total_batches:
            pct = batch_no / max(1, total_batches) * 100
            print(
                f"[{category}] {batch_no}/{total_batches} batches ({pct:5.1f}%) | "
                f"priced={len(priced_tickers):,} | passed={len(results):,}"
            )
        time.sleep(random.uniform(*PRIMARY_BATCH_SLEEP))

    retry = [t for t in dict.fromkeys(missing) if t not in results and t in by_ticker]
    if retry:
        print(f"[{category}] retrying {len(retry):,} symbols with bounded backoff")

        remaining = retry
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            if not remaining:
                break

            print(f"[{category}] retry attempt {attempt}/{RETRY_ATTEMPTS}: {len(remaining):,} symbols")
            next_remaining = []

            for retry_no, batch in enumerate(chunks(remaining, RETRY_BATCH_SIZE), 1):
                try:
                    raw = download_batch(batch, timeout=55)
                except Exception as exc:
                    next_remaining.extend(batch)
                    print(
                        f"[{category}] retry attempt {attempt} batch {retry_no} failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    time.sleep(min(12.0, 2.5 * attempt))
                    continue

                for ticker in batch:
                    try:
                        frame = frame_for(raw, ticker)
                        if frame is not None and not frame.empty:
                            priced_tickers.add(ticker)
                            item, reason = analyze(by_ticker[ticker], frame, thresholds, restricted, size_cache)
                            if item is not None:
                                results[ticker] = item
                        else:
                            next_remaining.append(ticker)
                    except Exception:
                        next_remaining.append(ticker)

                if retry_no % 20 == 0:
                    print(
                        f"[{category}] retry attempt {attempt} batch {retry_no} | "
                        f"still-missing~{len(next_remaining):,}"
                    )
                time.sleep(random.uniform(*RETRY_BATCH_SLEEP))

            remaining = list(dict.fromkeys(next_remaining))
            if remaining and attempt < RETRY_ATTEMPTS:
                backoff = min(30.0, 5.0 * (2 ** (attempt - 1)))
                print(
                    f"[{category}] {len(remaining):,} symbols still missing; "
                    f"cooling down {backoff:.0f}s before next retry"
                )
                time.sleep(backoff)

        if remaining:
            print(
                f"[{category}] final unavailable symbols after retries: "
                f"{len(remaining):,}"
            )

    expected_price_count = len(scan_universe)
    coverage = len(priced_tickers) / max(1, expected_price_count)
    required_coverage = MIN_COVERAGE[category]

    if len(priced_tickers) < 100 or coverage < required_coverage:
        raise RuntimeError(
            f"{category} price coverage too low: "
            f"{len(priced_tickers)}/{expected_price_count} ({coverage:.1%}), "
            f"required>={required_coverage:.0%}. Existing site data was not overwritten."
        )

    if category == "US_ETF":
        size_attempted = 0
        size_success = 0
        size_coverage = np.nan
    else:
        size_attempted = rejection["market_size_lt_10t"] + rejection["market_size_unavailable"] + len(results)
        size_success = rejection["market_size_lt_10t"] + len(results)
        size_coverage = size_success / max(1, size_attempted)
        if size_attempted >= 10 and size_coverage < MARKET_SIZE_MIN_LOOKUP_COVERAGE:
            raise RuntimeError(
                f"{category} market-size lookup coverage too low: "
                f"{size_success}/{size_attempted} ({size_coverage:.1%}), "
                f"required>={MARKET_SIZE_MIN_LOOKUP_COVERAGE:.0%}. Existing site data was not overwritten."
            )

    # Keep every hard-eligible symbol in summary; there is no score or %B floor.
    items = sorted(results.values(), key=lambda x: (-x["score"], x["symbol"]))
    for rank, item in enumerate(items, 1):
        item["rank"] = rank

    market_date = max((x["date"] for x in items if x.get("date")), default=None)
    payload_meta = {
        "app": "Morning Invest",
        "strategy": "MI_V8_5_BB_SWING200_WEEKLY_PSAR_FLIP_DECAY_DAILY_HA20_MA60_BT70_TOP20UI_ALLSEARCH",
        "category": category,
        "category_label": CATEGORY_LABEL[category],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market_date": market_date,
        "universe_source": universe_source,
        "restriction_snapshot": restriction_meta,
        "universe_count": len(universe),
        "price_download_universe_count": expected_price_count,
        "priced_count": len(priced_tickers),
        "coverage_pct": round(coverage * 100, 1),
        "passed_count": len(items),
        "market_size_min_krw": None if category == "US_ETF" else MIN_MARKET_SIZE_KRW,
        "market_size_filter": "exempt" if category == "US_ETF" else "krw_10t_min",
        "market_size_lookup_coverage_pct": round(size_coverage * 100, 1) if size_attempted and np.isfinite(size_coverage) else None,
        "thresholds": thresholds,
        "filter_counts": dict(sorted(rejection.items())),
        "max_score": RAW_MAX_SCORE,
        "display_score_multiplier": DISPLAY_SCORE_MULTIPLIER,
        "max_display_score": DISPLAY_MAX_SCORE,
        "swing_model": {
            "upper_band_percent_b": 0.95,
            "lookback_sessions": SWING_LOOKBACK_DAYS,
            "full_score_sessions": SWING_FULL_SCORE_DAYS,
            "max_score": SWING_MAX_SCORE,
            "min_score_at_lookback": SWING_MIN_SCORE,
        },
        "daily_ha_model": {
            "max_score": 1.0,
            "bearish_streak_cap_days": DAILY_HA_STREAK_CAP,
            "score_per_bearish_day": DAILY_HA_UNIT,
            "reversal_freshness_sessions": DAILY_HA_MAX_AGE,
        },
        "weekly_psar_model": {
            "current_week_included": True,
            "flip_definition": "weekly_psar_above_to_below",
            "score_by_weeks_since_flip": {
                "0": 0.5,
                "1": 0.4,
                "2": 0.3,
                "3": 0.2,
                "4": 0.1,
                "5_plus": 0.0
            },
        },
        "ma60_model": {
            "positive_slope_score": MA60_SCORE,
            "slope_definition": "today_MA60_minus_previous_trading_day_MA60",
        },
        "backtest_model": {
            "history": "max_1_trading_year",
            "min_signal_score": BACKTEST_MIN_SCORE,
            "min_signal_display_score": BACKTEST_MIN_DISPLAY_SCORE,
            "entry": "next_trading_day_open",
            "forward_sessions": [5, 10, 20],
            "cooldown_days": BACKTEST_COOLDOWN_DAYS,
            "historical_regulatory_status": "not_reconstructed"
        },
    }
    out_dir, detail_count = _write_category_site(category, payload_meta, items, size_cache)
    bundle_mb = (out_dir / "bundle.zip").stat().st_size / (1024 * 1024)
    summary_kb = (out_dir / "summary.json").stat().st_size / 1024
    print(
        f"[{category}] wrote {out_dir} | passed={detail_count:,} | "
        f"coverage={coverage:.1%} | summary={summary_kb:.1f}KB | bundle={bundle_mb:.1f}MB"
    )


def main():
    parser = argparse.ArgumentParser(description="Morning Invest daily technical screener")
    parser.add_argument(
        "--market",
        choices=["KR", "US", "US_ETF", "US_GROUP", "ALL"],
        default="ALL",
    )
    args = parser.parse_args()

    if args.market == "ALL":
        categories = ["KR", "US", "US_ETF"]
    elif args.market == "US_GROUP":
        categories = ["US", "US_ETF"]
    else:
        categories = [args.market]

    usdkrw = fetch_usdkrw() if any(c != "KR" for c in categories) else None
    if usdkrw:
        print(f"USD/KRW: {usdkrw:.4f}")

    failures = []
    for category in categories:
        try:
            scan_category(category, usdkrw=usdkrw)
        except Exception as exc:
            failures.append((category, str(exc)))
            print(f"ERROR {category}: {type(exc).__name__}: {exc}")
            traceback.print_exc()

    if failures:
        print("=" * 72)
        print("SCAN FAILURES / FALLBACK CHECK")
        unresolved = []

        for failed_category, message in failures:
            category_dir = DATA_DIR / CATEGORY_DIR[failed_category]
            summary_ok = (category_dir / "summary.json").is_file() and (category_dir / "summary.json").stat().st_size > 0
            bundle_ok = (category_dir / "bundle.zip").is_file() and (category_dir / "bundle.zip").stat().st_size > 0
            stocks_ok = (category_dir / "stocks").is_dir()

            if summary_ok and bundle_ok and stocks_ok:
                print(
                    f" - {failed_category}: fresh scan failed ({message}) "
                    "-> retaining hydrated previous snapshot"
                )
            else:
                print(
                    f" - {failed_category}: fresh scan failed ({message}) "
                    "-> NO VALID FALLBACK SNAPSHOT"
                )
                unresolved.append(failed_category)

        print("=" * 72)

        if unresolved:
            print(
                "Unresolved categories have no fresh output and no previous snapshot: "
                + ", ".join(unresolved)
            )
            raise SystemExit(1)

        print("Partial refresh is safe: failed categories retained their last valid snapshot.")


if __name__ == "__main__":
    main()
