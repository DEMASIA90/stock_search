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

MORNING_INVEST_COMPONENT_VERSION = "9.2"

import numpy as np
import pandas as pd
import yfinance as yf

from universe import Stock, fetch_kr_restricted_symbols, fetch_us_halted_symbols, get_universe

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ----------------------------
# Morning Invest strategy v9.1 — dual mode + intraday quick scan + KR ETF
# ----------------------------
# Fetch by explicit date range instead of relying on Yahoo's period parsing.
FULL_HISTORY_CALENDAR_DAYS = 1150
QUICK_HISTORY_CALENDAR_DAYS = 430
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
    "KR_ETF": 0.95,
    "US": 0.95,
    "US_ETF": 0.95,
}

BB_WINDOW = 20
BB_SIGMA = 2.0
MIN_TRADING_DAYS = 250
MIN_PRICE_KRW = 1_000.0

# Score model.
SWING_LOOKBACK_DAYS = 200
SWING_FULL_SCORE_DAYS = 5  # ~1 trading week
SWING_MAX_SCORE = 0.5
SWING_MIN_SCORE = 0.1
DAILY_HA_MAX_AGE = 3
DAILY_HA_STREAK_CAP = 20
DAILY_HA_UNIT = 0.05  # 20 consecutive bearish HA days -> 1.0
WEEKLY_HA_BULL_SCORE = 0.50
MONTHLY_HA_BULL_SCORE = 0.50
MA60_SCORE = 0.50
BOLLINGER_MAX_SCORE = 2.00

# "싼게 좋아" raw max = 5.0.
CHEAP_RAW_MAX_SCORE = 5.0
CHEAP_DISPLAY_MULTIPLIER = 100.0 / CHEAP_RAW_MAX_SCORE

# "오르는게 좋아" score model.
RISING_BREAKOUT_LOOKBACK_DAYS = 60
RISING_BREAKOUT_MAX_SCORE = 2.00
RISING_BREAKOUT_DAY5_SCORE = 1.00
RISING_DAILY_HA_SCORE = 0.50
RISING_WEEKLY_HA_SCORE = 0.25
RISING_MONTHLY_HA_SCORE = 0.25
RISING_VOLUME_PROFILE_SCORE = 0.50
RISING_POST_BREAKOUT_MAX_SCORE = 1.00
RISING_VOLUME_PROFILE_DAYS = 60
RISING_RAW_MAX_SCORE = 4.50
RISING_DISPLAY_MULTIPLIER = 100.0 / RISING_RAW_MAX_SCORE
DISPLAY_MAX_SCORE = 100.0

# Backtest: each mode is recreated point-in-time on each historical daily close.
# Signal is actionable only from the next trading day's open.
BACKTEST_LOOKBACK_DAYS = 252
BACKTEST_MIN_DISPLAY_SCORE = 50.0
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
ETF_CATEGORIES = {"KR_ETF", "US_ETF"}


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
    # Volume is not used by the cheap/bottom mode. It is retained only for
    # the rising-mode 60-session volume-profile approximation.
    if "Volume" in out.columns:
        out["Volume"] = pd.to_numeric(out["Volume"], errors="coerce").fillna(0.0).clip(lower=0.0)
    else:
        out["Volume"] = 0.0
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    out = out[(out["Close"] > 0) & (out["High"] > 0) & (out["Low"] > 0)]
    return out



def completed_daily(frame: pd.DataFrame, category: str, include_active_day: bool = False) -> pd.DataFrame:
    """Return daily bars appropriate for FULL or QUICK scans.

    FULL scans use only completed sessions. QUICK scans intentionally keep the
    current unfinished daily bar so rankings can move during the session.
    """
    if frame.empty or include_active_day:
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


def _resample_ohlc(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        frame.resample(rule)
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
        .dropna(subset=["Open", "High", "Low", "Close"])
    )



def active_weekly(frame: pd.DataFrame) -> pd.DataFrame:
    """Weekly OHLC including the current unfinished week from available daily bars."""
    return _resample_ohlc(frame, "W-FRI")


def active_monthly(frame: pd.DataFrame) -> pd.DataFrame:
    """Monthly OHLC including the current unfinished month from available daily bars."""
    return _resample_ohlc(frame, "ME")


def active_ha_bull_score(frame: pd.DataFrame, rule: str, score: float) -> tuple[float, bool]:
    """Score the current unfinished weekly/monthly Heikin-Ashi bar when bullish."""
    sampled = _resample_ohlc(frame, rule)
    if sampled.empty:
        return 0.0, False
    ha = heikin_ashi(sampled)
    bull = bool(ha["HA_Bull"].iloc[-1]) if not ha.empty else False
    return (float(score) if bull else 0.0), bull



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
    """Linear Bollinger lower-half score, max 2.0.

    %B=0.50 (middle band) or above -> 0.0
    %B=0.25 -> 1.0
    %B=0.00 (lower band) or below -> 2.0

    Bandwidth is retained as a diagnostic only; there is no squeeze penalty.
    """
    if not np.isfinite(percent_b):
        return 0.0, False
    if percent_b >= 0.5:
        score = 0.0
    elif percent_b <= 0.0:
        score = BOLLINGER_MAX_SCORE
    else:
        score = BOLLINGER_MAX_SCORE * (0.5 - percent_b) / 0.5
    return round(float(score), 6), False

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



def _rising_breakout_score_from_age(age: int | None) -> float:
    """Recency score for the latest bullish close/MA60 crossover.

    Today or 1 session ago = 2.00.
    2/3/4/5 sessions ago = 1.75/1.50/1.25/1.00.
    Older crossover can keep the mode eligible for the 60-session follow-through
    model, but earns zero recency points.
    """
    if age is None or age < 0:
        return 0.0
    if age <= 1:
        return RISING_BREAKOUT_MAX_SCORE
    if age <= 5:
        return round(
            RISING_BREAKOUT_MAX_SCORE
            - (RISING_BREAKOUT_MAX_SCORE - RISING_BREAKOUT_DAY5_SCORE) * ((age - 1) / 4.0),
            6,
        )
    return 0.0


def _latest_ma60_bull_cross(ind: pd.DataFrame) -> tuple[bool, int | None, int | None]:
    """Find latest close crossing from <=MA60 to >MA60 within 60 sessions.

    Rising mode is currently eligible only when the latest close is still above
    MA60 and such a bullish crossover exists within the last 60 sessions.
    """
    if len(ind) < 61:
        return False, None, None
    close = pd.to_numeric(ind["Close"], errors="coerce").to_numpy(dtype=float)
    ma60 = pd.to_numeric(ind["MA60"], errors="coerce").to_numpy(dtype=float)
    last = len(ind) - 1
    if not (np.isfinite(close[last]) and np.isfinite(ma60[last]) and close[last] > ma60[last]):
        return False, None, None
    start = max(1, last - RISING_BREAKOUT_LOOKBACK_DAYS)
    for i in range(last, start - 1, -1):
        if not all(np.isfinite(v) for v in (close[i], ma60[i], close[i-1], ma60[i-1])):
            continue
        if close[i] > ma60[i] and close[i-1] <= ma60[i-1]:
            return True, last - i, i
    return False, None, None


def _volume_profile_60(frame: pd.DataFrame, current_price: float) -> tuple[float, float, float, float | None]:
    """Approximate 60-session volume-at-price dominance using daily bars.

    Each daily bar's volume is assigned to its typical price (H+L+C)/3. The
    cumulative volume below the current price is compared with volume above it.
    This is a daily-bar proxy for a true intraday volume profile.
    """
    if len(frame) < RISING_VOLUME_PROFILE_DAYS or not np.isfinite(current_price):
        return 0.0, 0.0, 0.0, None
    w = frame.iloc[-RISING_VOLUME_PROFILE_DAYS:]
    vol = pd.to_numeric(w["Volume"], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
    tp = ((w["High"] + w["Low"] + w["Close"]) / 3.0).to_numpy(dtype=float)
    valid = np.isfinite(tp) & np.isfinite(vol) & (vol > 0)
    if not valid.any():
        return 0.0, 0.0, 0.0, None
    tp = tp[valid]
    vol = vol[valid]
    below = float(vol[tp < current_price].sum())
    above = float(vol[tp > current_price].sum())
    equal = float(vol[np.isclose(tp, current_price, rtol=1e-10, atol=1e-12)].sum())
    below += equal * 0.5
    above += equal * 0.5
    total = below + above
    below_share = below / total if total > 0 else None
    score = RISING_VOLUME_PROFILE_SCORE if total > 0 and below > above else 0.0
    return score, below, above, below_share


def score_rising_strategy(
    frame: pd.DataFrame,
    ind: pd.DataFrame,
    daily_ha: pd.DataFrame,
    weekly_ha_bull: bool,
    monthly_ha_bull: bool,
) -> tuple[float, dict, dict, bool]:
    """Current "오르는게 좋아" score, raw max 4.5."""
    eligible, cross_age, cross_idx = _latest_ma60_bull_cross(ind)
    close = finite(ind["Close"].iloc[-1])

    # 1) Bullish MA60 crossover recency.
    r1 = _rising_breakout_score_from_age(cross_age) if eligible else 0.0

    # 2) Current HA bullishness: D=.50, active W=.25, active M=.25.
    daily_bull = bool(daily_ha["HA_Bull"].iloc[-1]) if not daily_ha.empty else False
    r2_daily = RISING_DAILY_HA_SCORE if daily_bull else 0.0
    r2_weekly = RISING_WEEKLY_HA_SCORE if weekly_ha_bull else 0.0
    r2_monthly = RISING_MONTHLY_HA_SCORE if monthly_ha_bull else 0.0
    r2 = r2_daily + r2_weekly + r2_monthly

    # 3) 60-session volume profile below current price > above current price.
    r3, profile_below, profile_above, profile_below_share = _volume_profile_60(frame, close)

    # 4) Best high since latest bullish MA60 crossover, capped at 60 sessions.
    breakout_close = np.nan
    post_breakout_gain = np.nan
    r4 = 0.0
    if eligible and cross_idx is not None:
        breakout_close = finite(ind["Close"].iloc[cross_idx])
        end_idx = min(len(ind) - 1, cross_idx + RISING_BREAKOUT_LOOKBACK_DAYS)
        highest = finite(ind["High"].iloc[cross_idx:end_idx + 1].max())
        if np.isfinite(breakout_close) and breakout_close > 0 and np.isfinite(highest):
            post_breakout_gain = max(0.0, highest / breakout_close - 1.0)
            # 25% best rise => 0.25 point; >=100% => capped 1.00 point.
            r4 = min(RISING_POST_BREAKOUT_MAX_SCORE, post_breakout_gain)

    # Rule 1 is mandatory: no recent bullish MA60 crossover => mode score is zero.
    raw = round(r1 + r2 + r3 + r4, 4) if eligible else 0.0
    scores = {
        "r1_ma60_breakout": round(r1, 4),
        "r2_ha_bull": round(r2, 4),
        "r3_volume_profile": round(r3, 4),
        "r4_post_breakout_gain": round(r4, 4),
    }
    metrics = {
        "eligible": eligible,
        "breakout_age": cross_age,
        "breakout_close": clean(breakout_close),
        "daily_ha_bull": daily_bull,
        "weekly_ha_bull": weekly_ha_bull,
        "monthly_ha_bull": monthly_ha_bull,
        "ha_daily_score": round(r2_daily, 4),
        "ha_weekly_score": round(r2_weekly, 4),
        "ha_monthly_score": round(r2_monthly, 4),
        "volume_profile_below": clean(profile_below, 0),
        "volume_profile_above": clean(profile_above, 0),
        "volume_profile_below_share": clean(profile_below_share, 4),
        "post_breakout_max_gain": clean(post_breakout_gain, 5),
        "ma60": clean(ind["MA60"].iloc[-1]),
    }
    return raw, scores, metrics, eligible

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
    if category in {"KR", "KR_ETF"}:
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
    if stock.category in ETF_CATEGORIES:
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
    if stock.category in ETF_CATEGORIES:
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
    scan_mode: str = "FULL",
) -> tuple[dict | None, str]:
    if stock.symbol in restricted_symbols or stock.ticker in restricted_symbols:
        return None, "restricted_status"

    frame = completed_daily(_numeric_ohlc(raw_frame), stock.category, include_active_day=(scan_mode == "QUICK"))
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

    # Common hard filter. US ETFs remain market-size exempt.
    if stock.category in ETF_CATEGORIES:
        market_size_native, market_size_krw, market_size_basis = np.nan, np.nan, "exempt"
    else:
        size_info = resolve_market_size(stock, close, thresholds, size_cache)
        if size_info is None:
            return None, "market_size_unavailable"
        market_size_native, market_size_krw, market_size_basis = size_info
        if market_size_krw < MIN_MARKET_SIZE_KRW:
            return None, "market_size_lt_10t"

    # Shared HA states; active weekly/monthly bars include current incomplete period.
    daily_ha = heikin_ashi(frame)
    _, weekly_ha_bull = active_ha_bull_score(frame, "W-FRI", WEEKLY_HA_BULL_SCORE)
    _, monthly_ha_bull = active_ha_bull_score(frame, "ME", MONTHLY_HA_BULL_SCORE)

    # ------------------------------------------------------------------
    # Mode A: "싼게 좋아" — existing v8.7 bottom strategy.
    # ------------------------------------------------------------------
    s1, squeeze = score_percent_b(percent_b, bandwidth)
    s2, upper_swing_age = score_prior_upper_swing(ind["PercentB"])
    s3, d_ha_age, d_ha_bear_streak = ha_reversal_score(
        daily_ha,
        max_age=DAILY_HA_MAX_AGE,
        unit=DAILY_HA_UNIT,
        streak_cap=DAILY_HA_STREAK_CAP,
    )
    s4 = WEEKLY_HA_BULL_SCORE if weekly_ha_bull else 0.0
    s5 = MONTHLY_HA_BULL_SCORE if monthly_ha_bull else 0.0
    ma60 = finite(ind["MA60"].iloc[-1])
    ma60_prev = finite(ind["MA60"].iloc[-2]) if len(ind) >= 2 else np.nan
    ma60_slope = ma60 - ma60_prev if np.isfinite(ma60) and np.isfinite(ma60_prev) else np.nan
    ma60_slope_pct = (ma60 / ma60_prev - 1.0) if np.isfinite(ma60) and np.isfinite(ma60_prev) and ma60_prev != 0 else np.nan
    s6 = MA60_SCORE if np.isfinite(ma60_slope) and ma60_slope > 0 else 0.0
    cheap_total = round(s1 + s2 + s3 + s4 + s5 + s6, 4)

    cheap_scores = {
        "s1_percent_b": round(s1, 4),
        "s2_upper_swing": round(s2, 4),
        "s3_daily_ha": round(s3, 4),
        "s4_weekly_ha": round(s4, 4),
        "s5_monthly_ha": round(s5, 4),
        "s6_ma60_slope": round(s6, 4),
    }
    cheap_metrics = {
        "percent_b": clean(percent_b, 4),
        "bandwidth": clean(bandwidth, 4),
        "squeeze": squeeze,
        "bb_lower": clean(row["BB_Lower"]),
        "bb_mid": clean(row["BB_Mid"]),
        "bb_upper": clean(row["BB_Upper"]),
        "upper_swing_age": upper_swing_age,
        "daily_ha_age": d_ha_age,
        "daily_ha_prior_bear": d_ha_bear_streak,
        "weekly_ha_bull": weekly_ha_bull,
        "monthly_ha_bull": monthly_ha_bull,
        "ma60": clean(ma60),
        "ma60_slope": clean(ma60_slope, 6),
        "ma60_slope_pct": clean(ma60_slope_pct, 6),
        "market_size_native": clean(market_size_native, 0),
        "market_size_krw": clean(market_size_krw, 0),
        "market_size_basis": market_size_basis,
    }

    # ------------------------------------------------------------------
    # Mode B: "오르는게 좋아".
    # ------------------------------------------------------------------
    rising_total, rising_scores, rising_metrics, rising_eligible = score_rising_strategy(
        frame, ind, daily_ha, weekly_ha_bull, monthly_ha_bull
    )
    rising_metrics.update({
        "market_size_native": clean(market_size_native, 0),
        "market_size_krw": clean(market_size_krw, 0),
        "market_size_basis": market_size_basis,
    })

    # FULL refresh recalculates historical backtests. QUICK refresh updates only
    # current scores and preserves the last FULL backtest from Hosting.
    if scan_mode == "FULL":
        hist_sets = build_historical_score_sets(frame, stock.category, thresholds)
        cheap_bt = backtest_stock_from_hist(
            frame, hist_sets["cheap"], cheap_total, CHEAP_RAW_MAX_SCORE, mode="cheap"
        )
        rising_bt = backtest_stock_from_hist(
            frame, hist_sets["rising"], rising_total, RISING_RAW_MAX_SCORE, mode="rising"
        )
    else:
        cheap_bt = {}
        rising_bt = {}

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

        # Cheap mode remains top-level for backward compatibility.
        "score": cheap_total,
        "display_score": round(cheap_total * CHEAP_DISPLAY_MULTIPLIER, 1),
        "scores": cheap_scores,
        "metrics": cheap_metrics,
        "backtest": cheap_bt,

        # Rising mode is a parallel strategy payload.
        "rising": {
            "eligible": bool(rising_eligible),
            "rank": None,
            "score": rising_total,
            "display_score": round(rising_total * RISING_DISPLAY_MULTIPLIER, 1),
            "scores": rising_scores,
            "metrics": rising_metrics,
            "backtest": rising_bt,
        },
        "chart": _make_chart(ind),
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


def _active_period_ha_bull_series(frame: pd.DataFrame, freq: str) -> pd.Series:
    """Point-in-time bullish state of the unfinished weekly/monthly HA bar.

    The current period's OHLC contains only daily data available through each
    historical date, so the backtest does not use future week/month closes.
    """
    result = pd.Series(False, index=frame.index, dtype=bool)
    if frame.empty:
        return result

    periods = frame.index.to_period(freq)
    prev_ha_open = None
    prev_ha_close = None

    for _, group in frame.groupby(periods, sort=True):
        open_ = finite(group["Open"].iloc[0])
        run_high = -np.inf
        run_low = np.inf
        final_ha_open = None
        final_ha_close = None

        for idx, row in group.iterrows():
            run_high = max(run_high, finite(row["High"]))
            run_low = min(run_low, finite(row["Low"]))
            close_ = finite(row["Close"])
            ha_close = (open_ + run_high + run_low + close_) / 4.0
            if prev_ha_open is None or prev_ha_close is None:
                ha_open = (open_ + close_) / 2.0
            else:
                ha_open = (prev_ha_open + prev_ha_close) / 2.0
            result.at[idx] = bool(ha_close > ha_open)
            final_ha_open = ha_open
            final_ha_close = ha_close

        if final_ha_open is not None:
            prev_ha_open = float(final_ha_open)
            prev_ha_close = float(final_ha_close)

    return result

def _rising_breakout_series(ind: pd.DataFrame) -> pd.DataFrame:
    """Historical point-in-time MA60 bullish crossover state and follow-through."""
    result = pd.DataFrame(
        {"eligible": False, "age": np.nan, "r1": 0.0, "r4": 0.0, "gain": np.nan},
        index=ind.index,
    )
    close = pd.to_numeric(ind["Close"], errors="coerce").to_numpy(dtype=float)
    high = pd.to_numeric(ind["High"], errors="coerce").to_numpy(dtype=float)
    ma60 = pd.to_numeric(ind["MA60"], errors="coerce").to_numpy(dtype=float)
    last_cross = None
    breakout_close = np.nan
    max_high = np.nan

    for i in range(len(ind)):
        if i >= 1 and all(np.isfinite(v) for v in (close[i], ma60[i], close[i-1], ma60[i-1])):
            if close[i] > ma60[i] and close[i-1] <= ma60[i-1]:
                last_cross = i
                breakout_close = close[i]
                max_high = high[i] if np.isfinite(high[i]) else close[i]
            elif last_cross is not None and np.isfinite(high[i]):
                max_high = max(max_high, high[i]) if np.isfinite(max_high) else high[i]

        if last_cross is None:
            continue
        age = i - last_cross
        currently_above = np.isfinite(close[i]) and np.isfinite(ma60[i]) and close[i] > ma60[i]
        eligible = age <= RISING_BREAKOUT_LOOKBACK_DAYS and currently_above
        result.at[ind.index[i], "age"] = age
        result.at[ind.index[i], "eligible"] = eligible
        if not eligible:
            continue
        result.at[ind.index[i], "r1"] = _rising_breakout_score_from_age(age)
        if np.isfinite(breakout_close) and breakout_close > 0 and np.isfinite(max_high):
            gain = max(0.0, max_high / breakout_close - 1.0)
            result.at[ind.index[i], "gain"] = gain
            result.at[ind.index[i], "r4"] = min(RISING_POST_BREAKOUT_MAX_SCORE, gain)

    return result


def _volume_profile_dominance_series(frame: pd.DataFrame) -> pd.DataFrame:
    """Vectorized 60-session daily-bar volume profile for historical backtests."""
    result = pd.DataFrame(
        {"score": 0.0, "below": np.nan, "above": np.nan, "below_share": np.nan},
        index=frame.index,
    )
    n = len(frame)
    w = RISING_VOLUME_PROFILE_DAYS
    if n < w:
        return result

    close = pd.to_numeric(frame["Close"], errors="coerce").to_numpy(dtype=float)
    high = pd.to_numeric(frame["High"], errors="coerce").to_numpy(dtype=float)
    low = pd.to_numeric(frame["Low"], errors="coerce").to_numpy(dtype=float)
    vol = pd.to_numeric(frame["Volume"], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
    tp = (high + low + close) / 3.0

    tpw = np.lib.stride_tricks.sliding_window_view(tp, w)
    vw = np.lib.stride_tricks.sliding_window_view(vol, w)
    current = close[w-1:]
    valid = np.isfinite(tpw) & np.isfinite(vw) & (vw > 0)
    safe_v = np.where(valid, vw, 0.0)
    below = np.where(tpw < current[:, None], safe_v, 0.0).sum(axis=1)
    above = np.where(tpw > current[:, None], safe_v, 0.0).sum(axis=1)
    equal = np.where(np.isclose(tpw, current[:, None], rtol=1e-10, atol=1e-12), safe_v, 0.0).sum(axis=1)
    below = below + equal * 0.5
    above = above + equal * 0.5
    total = below + above
    share = np.divide(below, total, out=np.full_like(below, np.nan), where=total > 0)
    score = np.where((total > 0) & (below > above), RISING_VOLUME_PROFILE_SCORE, 0.0)

    idx = result.index[w-1:]
    result.loc[idx, "below"] = below
    result.loc[idx, "above"] = above
    result.loc[idx, "below_share"] = share
    result.loc[idx, "score"] = score
    return result


def build_historical_score_sets(
    frame: pd.DataFrame,
    category: str,
    thresholds: dict,
) -> dict[str, pd.DataFrame]:
    """Recreate both strategies at every historical close with no future data."""
    ind = add_daily_indicators(frame)
    daily_ha = heikin_ashi(frame)
    weekly_bull = _active_period_ha_bull_series(frame, "W-FRI")
    monthly_bull = _active_period_ha_bull_series(frame, "M")
    enough_history = pd.Series(np.arange(len(ind)) >= (MIN_TRADING_DAYS - 1), index=ind.index)
    common_eligible = enough_history & (ind["Close"] >= thresholds["min_price"])

    # ---------------- cheap / bottom ----------------
    cheap = pd.DataFrame(index=ind.index)
    pb = pd.to_numeric(ind["PercentB"], errors="coerce")
    s1 = pd.Series(0.0, index=ind.index)
    valid_pb = pb.notna()
    s1.loc[valid_pb & (pb <= 0.0)] = BOLLINGER_MAX_SCORE
    middle = valid_pb & (pb > 0.0) & (pb < 0.5)
    s1.loc[middle] = BOLLINGER_MAX_SCORE * (0.5 - pb.loc[middle]) / 0.5
    cheap["s1"] = s1

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
    cheap["s2"] = pd.Series(s2_values, index=ind.index)

    d_hist = _ha_reversal_score_series(
        daily_ha,
        max_age=DAILY_HA_MAX_AGE,
        unit=DAILY_HA_UNIT,
        streak_cap=DAILY_HA_STREAK_CAP,
    )
    cheap["s3"] = d_hist["score"]
    cheap["s4"] = weekly_bull.astype(float) * WEEKLY_HA_BULL_SCORE
    cheap["s5"] = monthly_bull.astype(float) * MONTHLY_HA_BULL_SCORE
    ma60_slope = pd.to_numeric(ind["MA60_Slope"], errors="coerce")
    cheap["s6"] = (ma60_slope > 0).astype(float) * MA60_SCORE
    cheap["score"] = cheap[["s1","s2","s3","s4","s5","s6"]].sum(axis=1)
    cheap["eligible"] = common_eligible & cheap["score"].notna()

    # ---------------- rising / momentum ----------------
    rising = pd.DataFrame(index=ind.index)
    breakout = _rising_breakout_series(ind)
    rising["r1"] = breakout["r1"]
    rising["r2"] = (
        daily_ha["HA_Bull"].astype(float) * RISING_DAILY_HA_SCORE
        + weekly_bull.astype(float) * RISING_WEEKLY_HA_SCORE
        + monthly_bull.astype(float) * RISING_MONTHLY_HA_SCORE
    )
    vp = _volume_profile_dominance_series(frame)
    rising["r3"] = vp["score"]
    rising["r4"] = breakout["r4"]
    rising["score"] = rising[["r1","r2","r3","r4"]].sum(axis=1)
    # Rule 1 is mandatory for backtest eligibility.
    rising["eligible"] = common_eligible & breakout["eligible"].astype(bool) & rising["score"].notna()

    return {"cheap": cheap, "rising": rising}


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

def backtest_stock_from_hist(
    frame: pd.DataFrame,
    hist: pd.DataFrame,
    current_score: float,
    raw_max_score: float,
    mode: str,
) -> dict:
    """Single-stock event backtest for one strategy mode."""
    if len(frame) < MIN_TRADING_DAYS + 25:
        return {
            "available": False,
            "reason": "insufficient_history",
            "signals": 0,
            "trades": [],
            "mode": mode,
            "raw_max_score": raw_max_score,
            "min_signal_display_score": BACKTEST_MIN_DISPLAY_SCORE,
        }

    min_signal_score = raw_max_score * BACKTEST_MIN_DISPLAY_SCORE / 100.0
    n = len(frame)
    start = max(MIN_TRADING_DAYS - 1, n - BACKTEST_LOOKBACK_DAYS)
    end = n - 21

    trades = []
    last_signal = -10_000
    for i in range(start, max(start, end)):
        if not bool(hist["eligible"].iloc[i]):
            continue
        if finite(hist["score"].iloc[i], -np.inf) < min_signal_score:
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

        path20 = []
        for step in range(20):
            pos = entry_i + step
            if pos >= len(frame):
                break
            path20.append(clean(frame["Close"].iloc[pos] / entry - 1.0, 6))

        trades.append({
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
        })
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

    public_trades = [
        {k: v for k, v in trade.items() if not k.startswith("_")}
        for trade in trades[-BACKTEST_RECENT_TRADES:][::-1]
    ]

    return {
        "available": True,
        "mode": mode,
        "raw_max_score": raw_max_score,
        "lookback_days": BACKTEST_LOOKBACK_DAYS,
        "min_signal_score": min_signal_score,
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
            "historical_market_cap_not_reconstructed",
            *( ["volume_profile_uses_daily_typical_price_proxy"] if mode == "rising" else [] ),
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
        return set(), {"source": "KRX_ETF_MASTER", "restricted_count": 0}
    halted, meta = fetch_us_halted_symbols()
    return halted, meta



def _detail_filename(item: dict) -> str:
    symbol = re.sub(r"[^A-Za-z0-9._-]+", "_", str(item.get("symbol") or "stock")).strip("._-")
    symbol = (symbol or "stock")[:36]
    digest = hashlib.sha1(str(item["ticker"]).encode("utf-8")).hexdigest()[:12]
    return f"{symbol}-{digest}.json"


def _compact_backtest(bt: dict) -> dict:
    forecast = (bt or {}).get("forecast") or {}
    return {
        "available": bool((bt or {}).get("available")),
        "signals": (bt or {}).get("signals"),
        "avg_20d": (bt or {}).get("avg_20d"),
        "win_20d": (bt or {}).get("win_20d"),
        "quality_score": (bt or {}).get("quality_score"),
        "quality_label": (bt or {}).get("quality_label", "NORMAL"),
        "forecast_available": bool(forecast.get("available")),
        "preserved_from_full": bool((bt or {}).get("preserved_from_full")),
    }


def _summary_item(item: dict, detail_path: str) -> dict:
    """Small row payload used by the initial market screen; both modes included."""
    rising = item.get("rising") or {}
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

        # Cheap mode (legacy top-level schema).
        "rank": item["rank"],
        "score": item["score"],
        "display_score": item.get("display_score"),
        "scores": item["scores"],
        "market_size_krw": (item.get("metrics") or {}).get("market_size_krw"),
        "market_size_basis": (item.get("metrics") or {}).get("market_size_basis"),
        "backtest": _compact_backtest(item.get("backtest") or {}),

        # Rising mode.
        "rising": {
            "eligible": bool(rising.get("eligible")),
            "rank": rising.get("rank"),
            "score": rising.get("score", 0.0),
            "display_score": rising.get("display_score", 0.0),
            "scores": rising.get("scores") or {},
            "metrics": {
                "breakout_age": (rising.get("metrics") or {}).get("breakout_age"),
                "post_breakout_max_gain": (rising.get("metrics") or {}).get("post_breakout_max_gain"),
                "volume_profile_below_share": (rising.get("metrics") or {}).get("volume_profile_below_share"),
            },
            "backtest": _compact_backtest(rising.get("backtest") or {}),
        },
        "detail_path": detail_path,
    }


def _write_category_site(
    category: str,
    payload_meta: dict,
    items: list[dict],
    size_cache: dict,
    scan_mode: str = "FULL",
) -> tuple[Path, int]:
    """Write summary/details while preserving FULL backtests on QUICK refreshes."""
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
        print(f"[{category}] QUICK: preserved backtests for {len(previous_detail_payloads):,} prior details")

    shutil.rmtree(category_dir, ignore_errors=True)
    stocks_dir = category_dir / "stocks"
    stocks_dir.mkdir(parents=True, exist_ok=True)

    summary_items = []
    for item in items:
        if scan_mode == "QUICK":
            previous = previous_detail_payloads.get(str(item.get("ticker"))) or {}
            if previous.get("backtest"):
                item["backtest"] = dict(previous["backtest"])
                item["backtest"]["preserved_from_full"] = True
                # Forecast prices depend on the current score/price, so do not
                # present yesterday's forecast as if it were refreshed intraday.
                item["backtest"]["forecast"] = {"available": False, "reason": "quick_scan_not_recomputed"}
            previous_rising = previous.get("rising") or {}
            if previous_rising.get("backtest"):
                item.setdefault("rising", {})["backtest"] = dict(previous_rising["backtest"])
                item["rising"]["backtest"]["preserved_from_full"] = True
                item["rising"]["backtest"]["forecast"] = {"available": False, "reason": "quick_scan_not_recomputed"}

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

def scan_category(category: str, usdkrw: float | None = None, scan_mode: str = "FULL") -> None:
    universe, universe_source = get_universe(category)
    thresholds = thresholds_for(category, usdkrw)
    restricted, restriction_meta = _load_restrictions(category, universe)
    size_cache = _load_size_cache(category)

    print("=" * 72)
    print(f"Morning Invest | {category} | mode={scan_mode} | universe={len(universe):,} | restricted={len(restricted):,}")
    size_rule = (
        "market size filter=OFF (ETF exempt)"
        if category in ETF_CATEGORIES
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
            raw = download_batch(tickers, scan_mode=scan_mode)
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
                item, reason = analyze(stock, frame, thresholds, restricted, size_cache, scan_mode=scan_mode)
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
        retry_attempts = 1 if scan_mode == "QUICK" else RETRY_ATTEMPTS
        for attempt in range(1, retry_attempts + 1):
            if not remaining:
                break

            print(f"[{category}] retry attempt {attempt}/{retry_attempts}: {len(remaining):,} symbols")
            next_remaining = []

            for retry_no, batch in enumerate(chunks(remaining, RETRY_BATCH_SIZE), 1):
                try:
                    raw = download_batch(batch, scan_mode=scan_mode, timeout=55)
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
                            item, reason = analyze(by_ticker[ticker], frame, thresholds, restricted, size_cache, scan_mode=scan_mode)
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

            previous_remaining = set(remaining)
            remaining = list(dict.fromkeys(next_remaining))

            # If a full retry did not recover even one ticker, the remaining set
            # is overwhelmingly likely to be delisted/unsupported rather than a
            # transient batch failure.  Do not hammer Yahoo with the same symbols
            # two more times; the market coverage gate below remains the safety net.
            if remaining and set(remaining) == previous_remaining:
                print(
                    f"[{category}] retry made no progress ({len(remaining):,} unchanged); "
                    "stopping repeated retries for this permanent-missing set"
                )
                break

            if remaining and attempt < retry_attempts:
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

    if category in ETF_CATEGORIES:
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

    # Keep every hard-eligible symbol in summary so search can always find it.
    # Each mode gets an independent ranking.
    items = sorted(results.values(), key=lambda x: (-x["score"], x["symbol"]))
    for rank, item in enumerate(items, 1):
        item["rank"] = rank

    rising_items = sorted(
        [x for x in items if bool((x.get("rising") or {}).get("eligible"))],
        key=lambda x: (-float((x.get("rising") or {}).get("score", 0.0)), x["symbol"]),
    )
    for item in items:
        (item.get("rising") or {})["rank"] = None
    for rank, item in enumerate(rising_items, 1):
        item["rising"]["rank"] = rank

    market_date = max((x["date"] for x in items if x.get("date")), default=None)
    payload_meta = {
        "app": "Morning Invest",
        "strategy": "MI_V9_2_DUAL_QUICK_FULL_KR_ETF_HOTFIX",
        "modes": ["cheap", "rising"],
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
        "rising_eligible_count": len(rising_items),
        "market_size_min_krw": None if category in ETF_CATEGORIES else MIN_MARKET_SIZE_KRW,
        "market_size_filter": "exempt" if category in ETF_CATEGORIES else "krw_10t_min",
        "market_size_lookup_coverage_pct": round(size_coverage * 100, 1) if size_attempted and np.isfinite(size_coverage) else None,
        "thresholds": thresholds,
        "filter_counts": dict(sorted(rejection.items())),
        "max_score": CHEAP_RAW_MAX_SCORE,
        "display_score_multiplier": CHEAP_DISPLAY_MULTIPLIER,
        "max_display_score": DISPLAY_MAX_SCORE,
        "mode_max_scores": {"cheap": CHEAP_RAW_MAX_SCORE, "rising": RISING_RAW_MAX_SCORE},
        "swing_model": {
            "upper_band_percent_b": 0.95,
            "lookback_sessions": SWING_LOOKBACK_DAYS,
            "full_score_sessions": SWING_FULL_SCORE_DAYS,
            "max_score": SWING_MAX_SCORE,
            "min_score_at_lookback": SWING_MIN_SCORE,
        },
        "bollinger_model": {
            "max_score": BOLLINGER_MAX_SCORE,
            "zero_score_at_percent_b": 0.5,
            "full_score_at_or_below_percent_b": 0.0,
            "shape": "linear",
            "squeeze_penalty": False,
        },
        "daily_ha_model": {
            "max_score": 1.0,
            "bearish_streak_cap_days": DAILY_HA_STREAK_CAP,
            "score_per_bearish_day": DAILY_HA_UNIT,
            "reversal_freshness_sessions": DAILY_HA_MAX_AGE,
        },
        "weekly_ha_model": {
            "current_week_included": True,
            "bullish_score": WEEKLY_HA_BULL_SCORE,
        },
        "monthly_ha_model": {
            "current_month_included": True,
            "bullish_score": MONTHLY_HA_BULL_SCORE,
        },
        "ma60_model": {
            "positive_slope_score": MA60_SCORE,
            "slope_definition": "today_MA60_minus_previous_trading_day_MA60",
        },
        "rising_model": {
            "mandatory": "current_close_above_ma60_and_bullish_ma60_cross_within_60_sessions",
            "ma60_cross_recency": {"today_or_1d": 2.0, "2d": 1.75, "3d": 1.5, "4d": 1.25, "5d": 1.0, "6_to_60d": 0.0},
            "ha_bullish": {"daily": 0.5, "weekly_active": 0.25, "monthly_active": 0.25},
            "volume_profile": {"sessions": 60, "below_gt_above_score": 0.5, "method": "daily_typical_price_weighted_by_volume"},
            "post_breakout_gain": {"score_equals_gain_fraction": True, "cap": 1.0, "window_sessions": 60},
            "max_score": RISING_RAW_MAX_SCORE,
        },
        "backtest_model": {
            "history": "max_1_trading_year",
            "min_signal_display_score": BACKTEST_MIN_DISPLAY_SCORE,
            "entry": "next_trading_day_open",
            "forward_sessions": [5, 10, 20],
            "cooldown_days": BACKTEST_COOLDOWN_DAYS,
            "historical_regulatory_status": "not_reconstructed"
        },
    }
    out_dir, detail_count = _write_category_site(category, payload_meta, items, size_cache, scan_mode=scan_mode)
    bundle_mb = (out_dir / "bundle.zip").stat().st_size / (1024 * 1024)
    summary_kb = (out_dir / "summary.json").stat().st_size / 1024
    print(
        f"[{category}] wrote {out_dir} | passed={detail_count:,} | "
        f"coverage={coverage:.1%} | summary={summary_kb:.1f}KB | bundle={bundle_mb:.1f}MB"
    )


def main():
    parser = argparse.ArgumentParser(description="Morning Invest technical screener")
    parser.add_argument(
        "--market",
        choices=["KR", "KR_ETF", "KR_GROUP", "US", "US_ETF", "US_GROUP", "ALL"],
        default="ALL",
    )
    parser.add_argument(
        "--scan-mode",
        choices=["FULL", "QUICK"],
        default="FULL",
        help="FULL recalculates backtests; QUICK includes the active daily bar and preserves prior backtests.",
    )
    args = parser.parse_args()

    if args.market == "ALL":
        categories = ["KR", "KR_ETF", "US", "US_ETF"]
    elif args.market == "KR_GROUP":
        categories = ["KR", "KR_ETF"]
    elif args.market == "US_GROUP":
        categories = ["US", "US_ETF"]
    else:
        categories = [args.market]

    usdkrw = fetch_usdkrw() if any(c in {"US", "US_ETF"} for c in categories) else None
    if usdkrw:
        print(f"USD/KRW: {usdkrw:.4f}")

    failures = []
    for category in categories:
        try:
            scan_category(category, usdkrw=usdkrw, scan_mode=args.scan_mode)
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
