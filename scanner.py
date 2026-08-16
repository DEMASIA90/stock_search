from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import shutil
import time
import traceback
import zipfile
from collections import Counter
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MORNING_INVEST_COMPONENT_VERSION = "7.4"

import numpy as np
import pandas as pd
import yfinance as yf

from universe import Stock, fetch_kr_restricted_symbols, fetch_us_halted_symbols, get_universe

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ----------------------------
# Morning Invest strategy v4
# ----------------------------
PERIOD = "3y"
BATCH_SIZE = 32
RETRY_BATCH_SIZE = 8
CHART_POINTS = 120
MIN_COVERAGE = 0.20

BB_WINDOW = 20
BB_SIGMA = 2.0
MIN_TRADING_DAYS = 250
MIN_PRICE_KRW = 1_000.0
MIN_TURNOVER_20_KRW = 1_000_000_000.0
MAX_DAILY_PERCENT_B = 0.35
MONTHLY_HA_BEARISH_EXCLUDE = 10
SQUEEZE_BANDWIDTH = 0.08

# Backtest: current strategy recreated point-in-time on each historical daily close.
# Signal is actionable only from the next trading day's open.
BACKTEST_EVAL_DAYS = 504
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


def _numeric_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    out.index = pd.DatetimeIndex(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in out.columns:
            return pd.DataFrame()
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    out = out[(out["Close"] > 0) & (out["High"] > 0) & (out["Low"] > 0) & (out["Volume"] >= 0)]
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
    out["Turnover"] = close * out["Volume"]
    out["TurnoverAvg20"] = out["Turnover"].rolling(20).mean()
    out["Bull"] = out["Close"] > out["Open"]
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
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(subset=["Open", "High", "Low", "Close"])
    )


def confirmed_weekly(frame: pd.DataFrame, category: str) -> pd.DataFrame:
    weekly = _resample_ohlc(frame, "W-FRI")
    now = datetime.now(ZoneInfo(CATEGORY_TZ[category]))
    keep = []
    for idx in weekly.index:
        end_date = idx.date()
        if end_date < now.date():
            keep.append(True)
        elif end_date > now.date():
            keep.append(False)
        else:
            keep.append(now.time().replace(tzinfo=None) >= CATEGORY_CLOSE[category])
    return weekly[keep]


def confirmed_monthly(frame: pd.DataFrame, category: str) -> pd.DataFrame:
    # Month-end bars from the current calendar month are always excluded. This is
    # conservative and guarantees that only confirmed monthly candles are used.
    monthly = _resample_ohlc(frame, "ME")
    today = datetime.now(ZoneInfo(CATEGORY_TZ[category])).date()
    keep = [(idx.year, idx.month) != (today.year, today.month) for idx in monthly.index]
    return monthly[keep]


def consecutive_bearish_from_end(ha: pd.DataFrame) -> int:
    if ha.empty:
        return 0
    count = 0
    for value in reversed(ha["HA_Bull"].tolist()):
        if bool(value):
            break
        count += 1
    return count


def ha_reversal_score(ha: pd.DataFrame, max_age: int, unit: float, streak_cap: int) -> tuple[float, int | None, int]:
    """Most recent bearish->bullish HA transition within max_age completed bars."""
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
    if percent_b <= 0:
        score = 1.0
    else:
        score = min((1.0 - percent_b) ** 2, 1.0)
    squeeze = bool(np.isfinite(bandwidth) and bandwidth < SQUEEZE_BANDWIDTH)
    if squeeze:
        score *= 0.5
    return round(score, 6), squeeze


def score_prior_upper_swing(percent_b: pd.Series) -> tuple[float, int | None]:
    # Current bar is age 0. We accept any >=0.95 observation 5~40 trading days ago.
    values = pd.to_numeric(percent_b, errors="coerce").to_numpy(dtype=float)
    last = len(values) - 1
    found_ages = []
    for age in range(5, 41):
        i = last - age
        if i < 0:
            break
        if np.isfinite(values[i]) and values[i] >= 0.95:
            found_ages.append(age)
    return (0.3, min(found_ages)) if found_ages else (0.0, None)


def score_psar(frame: pd.DataFrame) -> tuple[float, int | None, int]:
    _, bull = parabolic_sar(frame, af_start=0.02, af_step=0.02, af_max=0.20)
    states = bull.to_numpy(dtype=bool)
    if len(states) < 7 or not states[-1]:
        return 0.0, None, 0

    last = len(states) - 1
    for transition in range(last, max(1, last - 3) - 1, -1):
        if states[transition] and not states[transition - 1]:
            prior_above = 0
            j = transition - 1
            while j >= 0 and not states[j]:
                prior_above += 1
                j -= 1
            age = last - transition
            if prior_above >= 5:
                return round(0.5 - 0.1 * age, 6), age, prior_above
            return 0.0, age, prior_above
    return 0.0, None, 0


def score_turnover(ind: pd.DataFrame) -> tuple[float, float, float]:
    turnover = pd.to_numeric(ind["Turnover"], errors="coerce").dropna()
    if len(turnover) < 120:
        return 0.0, np.nan, np.nan

    recent = turnover.iloc[-5:]
    prior = turnover.iloc[-120:-5]
    prior_mean = finite(prior.mean())
    recent_mean = finite(recent.mean())
    if prior_mean <= 0 or recent_mean <= 0:
        return 0.0, np.nan, np.nan

    r = recent_mean / prior_mean
    raw_score = clip(0.4 * math.log(r), -0.3, 0.3)

    recent_rows = ind.loc[recent.index]
    recent_total = finite(recent_rows["Turnover"].sum())
    bullish_turnover = finite(recent_rows.loc[recent_rows["Bull"], "Turnover"].sum(), 0.0)
    bull_share = bullish_turnover / recent_total if recent_total > 0 else np.nan

    if r > 1.5 and np.isfinite(bull_share) and bull_share < 0.35:
        raw_score = -raw_score
    return round(raw_score, 6), r, bull_share


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
            "min_turnover20": MIN_TURNOVER_20_KRW,
            "currency": "KRW",
            "usdkrw": None,
        }
    if not usdkrw:
        raise RuntimeError("USD/KRW is required for US thresholds")
    return {
        "min_price": MIN_PRICE_KRW / usdkrw,
        "min_turnover20": MIN_TURNOVER_20_KRW / usdkrw,
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
    }


def analyze(
    stock: Stock,
    raw_frame: pd.DataFrame,
    thresholds: dict,
    restricted_symbols: set[str],
) -> tuple[dict | None, str]:
    if stock.symbol in restricted_symbols or stock.ticker in restricted_symbols:
        return None, "restricted_status"

    frame = completed_daily(_numeric_ohlcv(raw_frame), stock.category)
    if frame.empty:
        return None, "no_price"
    if len(frame) < MIN_TRADING_DAYS:
        return None, "listed_lt_250d"

    ind = add_daily_indicators(frame)
    valid = ind.dropna(subset=["BB_Mid", "BB_Upper", "BB_Lower", "PercentB", "TurnoverAvg20"])
    if len(valid) < 120:
        return None, "indicator_history"

    row = valid.iloc[-1]
    close = finite(row["Close"])
    percent_b = finite(row["PercentB"])
    bandwidth = finite(row["Bandwidth"])
    turnover20 = finite(row["TurnoverAvg20"])

    if close < thresholds["min_price"]:
        return None, "price_lt_threshold"
    if turnover20 < thresholds["min_turnover20"]:
        return None, "turnover20_lt_threshold"
    if not np.isfinite(percent_b) or percent_b > MAX_DAILY_PERCENT_B:
        return None, "percent_b_gt_035"

    monthly = confirmed_monthly(frame, stock.category)
    monthly_ha = heikin_ashi(monthly)
    monthly_bearish_streak = consecutive_bearish_from_end(monthly_ha)
    if monthly_bearish_streak >= MONTHLY_HA_BEARISH_EXCLUDE:
        return None, "monthly_ha_bear_10plus"

    # 1. Daily %B score, with squeeze penalty.
    s1, squeeze = score_percent_b(percent_b, bandwidth)

    # 2. Previous upper-band swing in the 5~40 trading-day window.
    s2, upper_swing_age = score_prior_upper_swing(ind["PercentB"])

    # 3. PSAR: >=5 days above, then transition below, valid for d=0..3.
    s3, psar_age, psar_prior_above = score_psar(frame)

    # 4~6. HA reversal events on daily / completed weekly / completed monthly bars.
    daily_ha = heikin_ashi(frame)
    weekly = confirmed_weekly(frame, stock.category)
    weekly_ha = heikin_ashi(weekly)

    s4, d_ha_age, d_ha_bear_streak = ha_reversal_score(daily_ha, max_age=3, unit=0.05, streak_cap=5)
    s5, w_ha_age, w_ha_bear_streak = ha_reversal_score(weekly_ha, max_age=2, unit=0.10, streak_cap=4)
    s6, m_ha_age, m_ha_bear_streak = ha_reversal_score(monthly_ha, max_age=1, unit=0.15, streak_cap=3)

    # 7. Multi-timeframe HA cap.
    s7 = round(min(s4 + s5 + s6, 0.6), 6)

    # 8. Trading-value regime score.
    s8, turnover_r, bullish_turnover_share = score_turnover(ind)

    total = round(s1 + s2 + s3 + s7 + s8, 4)

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
        "scores": {
            "s1_percent_b": round(s1, 4),
            "s2_upper_swing": round(s2, 4),
            "s3_psar": round(s3, 4),
            "s4_daily_ha": round(s4, 4),
            "s5_weekly_ha": round(s5, 4),
            "s6_monthly_ha": round(s6, 4),
            "s7_ha_capped": round(s7, 4),
            "s8_turnover": round(s8, 4),
        },
        "metrics": {
            "percent_b": clean(percent_b, 4),
            "bandwidth": clean(bandwidth, 4),
            "squeeze": squeeze,
            "bb_lower": clean(row["BB_Lower"]),
            "bb_mid": clean(row["BB_Mid"]),
            "bb_upper": clean(row["BB_Upper"]),
            "turnover20": clean(turnover20, 0),
            "turnover_r": clean(turnover_r, 3),
            "bullish_turnover_share": clean(bullish_turnover_share, 3),
            "upper_swing_age": upper_swing_age,
            "psar_age": psar_age,
            "psar_prior_above": psar_prior_above,
            "daily_ha_age": d_ha_age,
            "daily_ha_prior_bear": d_ha_bear_streak,
            "weekly_ha_age": w_ha_age,
            "weekly_ha_prior_bear": w_ha_bear_streak,
            "monthly_ha_age": m_ha_age,
            "monthly_ha_prior_bear": m_ha_bear_streak,
            "monthly_ha_current_bear_streak": monthly_bearish_streak,
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


def _psar_score_series(frame: pd.DataFrame) -> pd.DataFrame:
    """Historical PSAR event score without look-ahead."""
    _, bull = parabolic_sar(frame, af_start=0.02, af_step=0.02, af_max=0.20)
    states = bull.to_numpy(dtype=bool)
    result = pd.DataFrame(
        {"score": 0.0, "age": np.nan, "prior_above": 0},
        index=frame.index,
    )
    if len(states) < 7:
        return result

    s_col = result.columns.get_loc("score")
    a_col = result.columns.get_loc("age")
    p_col = result.columns.get_loc("prior_above")

    for transition in range(1, len(states)):
        if not (states[transition] and not states[transition - 1]):
            continue
        prior_above = 0
        j = transition - 1
        while j >= 0 and not states[j]:
            prior_above += 1
            j -= 1
        if prior_above < 5:
            continue
        for age in range(4):
            pos = transition + age
            if pos >= len(result):
                break
            result.iat[pos, s_col] = 0.5 - 0.1 * age
            result.iat[pos, a_col] = age
            result.iat[pos, p_col] = prior_above
    return result


def _turnover_score_series(ind: pd.DataFrame) -> pd.DataFrame:
    turnover = pd.to_numeric(ind["Turnover"], errors="coerce")
    recent_mean = turnover.rolling(5, min_periods=5).mean()
    prior_mean = turnover.shift(5).rolling(115, min_periods=115).mean()
    r = recent_mean / prior_mean.replace(0, np.nan)

    raw = 0.4 * np.log(r)
    raw = raw.clip(-0.3, 0.3)

    bullish_value = ind["Turnover"].where(ind["Bull"], 0.0).rolling(5, min_periods=5).sum()
    total_value = ind["Turnover"].rolling(5, min_periods=5).sum()
    bull_share = bullish_value / total_value.replace(0, np.nan)

    flip = (r > 1.5) & (bull_share < 0.35)
    score = raw.where(~flip, -raw)

    return pd.DataFrame({"score": score, "r": r, "bull_share": bull_share}, index=ind.index)


def _bearish_streak_series(ha: pd.DataFrame) -> pd.Series:
    streaks = []
    streak = 0
    for bull in ha["HA_Bull"].astype(bool).tolist():
        if bull:
            streak = 0
        else:
            streak += 1
        streaks.append(streak)
    return pd.Series(streaks, index=ha.index, dtype=float)


def _map_last_confirmed(
    daily_index: pd.DatetimeIndex,
    source: pd.DataFrame,
    mode: str,
) -> pd.DataFrame:
    """Map the latest completed weekly/monthly row to each historical daily close."""
    if source.empty:
        return pd.DataFrame(index=daily_index, columns=source.columns)

    src_index = pd.DatetimeIndex(source.index)
    rows = []
    for d in daily_index:
        if mode == "weekly":
            pos = src_index.searchsorted(pd.Timestamp(d), side="right") - 1
        elif mode == "monthly":
            month_start = pd.Timestamp(year=d.year, month=d.month, day=1)
            pos = src_index.searchsorted(month_start, side="left") - 1
        else:
            raise ValueError(mode)

        if pos >= 0:
            rows.append(source.iloc[pos].to_dict())
        else:
            rows.append({c: np.nan for c in source.columns})
    return pd.DataFrame(rows, index=daily_index, columns=source.columns)


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

    # 2) upper-band observation 5~40 sessions ago
    prior_upper = pb.shift(5).rolling(36, min_periods=1).max()
    hist["s2"] = (prior_upper >= 0.95).astype(float) * 0.3

    # 3) PSAR transition event
    psar_hist = _psar_score_series(frame)
    hist["s3"] = psar_hist["score"]

    # 4) Daily HA transition
    daily_ha = heikin_ashi(frame)
    d_hist = _ha_reversal_score_series(daily_ha, max_age=3, unit=0.05, streak_cap=5)
    hist["s4"] = d_hist["score"]

    # 5) Completed weekly HA transition
    weekly = _resample_ohlc(frame, "W-FRI")
    weekly_ha = heikin_ashi(weekly)
    w_hist = _ha_reversal_score_series(weekly_ha, max_age=2, unit=0.10, streak_cap=4)
    w_daily = _map_last_confirmed(ind.index, w_hist, "weekly")
    hist["s5"] = pd.to_numeric(w_daily["score"], errors="coerce").fillna(0.0)

    # 6) Completed monthly HA transition + 0-step long bearish exclusion
    monthly = _resample_ohlc(frame, "ME")
    monthly_ha = heikin_ashi(monthly)
    m_hist = _ha_reversal_score_series(monthly_ha, max_age=1, unit=0.15, streak_cap=3)
    m_hist["bear_streak"] = _bearish_streak_series(monthly_ha)
    m_daily = _map_last_confirmed(ind.index, m_hist, "monthly")
    hist["s6"] = pd.to_numeric(m_daily["score"], errors="coerce").fillna(0.0)
    hist["monthly_bear_streak"] = pd.to_numeric(m_daily["bear_streak"], errors="coerce")

    # 7) HA cap
    hist["s7"] = (hist["s4"] + hist["s5"] + hist["s6"]).clip(upper=0.6)

    # 8) trading-value score
    t_hist = _turnover_score_series(ind)
    hist["s8"] = t_hist["score"].fillna(0.0)

    hist["score"] = hist["s1"] + hist["s2"] + hist["s3"] + hist["s7"] + hist["s8"]

    # 0-step point-in-time market-data filters.
    enough_history = pd.Series(np.arange(len(ind)) >= (MIN_TRADING_DAYS - 1), index=ind.index)
    hist["eligible"] = (
        enough_history
        & (ind["Close"] >= thresholds["min_price"])
        & (ind["TurnoverAvg20"] >= thresholds["min_turnover20"])
        & (ind["PercentB"] <= MAX_DAILY_PERCENT_B)
        & (hist["monthly_bear_streak"] < MONTHLY_HA_BEARISH_EXCLUDE)
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
    start = max(MIN_TRADING_DAYS - 1, n - BACKTEST_EVAL_DAYS - 21)
    end = n - 21  # need next open + full 20-session forward window

    trades = []
    last_signal = -10_000
    for i in range(start, max(start, end)):
        if not bool(hist["eligible"].iloc[i]):
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
        "eval_days": min(BACKTEST_EVAL_DAYS, max(0, end - start)),
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
        "limitations": ["historical_regulatory_status_not_reconstructed"],
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
    return yf.download(
        tickers=tickers,
        period=PERIOD,
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        actions=False,
        progress=False,
        threads=True,
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
        "scores": item["scores"],
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


def _write_category_site(category: str, payload_meta: dict, items: list[dict]) -> tuple[Path, int]:
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
        for detail_file in sorted(stocks_dir.glob("*.json")):
            zf.write(detail_file, f"stocks/{detail_file.name}")

    return category_dir, len(items)

def scan_category(category: str, usdkrw: float | None = None) -> None:
    universe, universe_source = get_universe(category)
    thresholds = thresholds_for(category, usdkrw)
    restricted, restriction_meta = _load_restrictions(category, universe)

    print("=" * 72)
    print(f"Morning Invest | {category} | universe={len(universe):,} | restricted={len(restricted):,}")
    print(
        f"thresholds: close>={thresholds['min_price']:.4f} {thresholds['currency']}, "
        f"20d turnover>={thresholds['min_turnover20']:.0f} {thresholds['currency']}"
    )
    print("=" * 72)

    results: dict[str, dict] = {}
    rejection = Counter()
    priced_tickers: set[str] = set()
    missing: list[str] = []
    by_ticker = {s.ticker: s for s in universe}

    batches = list(chunks(universe, BATCH_SIZE))
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
                item, reason = analyze(stock, frame, thresholds, restricted)
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
        time.sleep(random.uniform(0.18, 0.42))

    retry = [t for t in dict.fromkeys(missing) if t not in results]
    if retry:
        print(f"[{category}] retrying {len(retry):,} symbols")
        for retry_no, batch in enumerate(chunks(retry, RETRY_BATCH_SIZE), 1):
            try:
                raw = download_batch(batch, timeout=50)
            except Exception:
                time.sleep(1.0)
                continue
            for ticker in batch:
                try:
                    frame = frame_for(raw, ticker)
                    if frame is not None and not frame.empty:
                        priced_tickers.add(ticker)
                    item, reason = analyze(by_ticker[ticker], frame, thresholds, restricted)
                    if item is not None:
                        results[ticker] = item
                except Exception:
                    pass
            if retry_no % 10 == 0:
                print(f"[{category}] retry batch {retry_no}")
            time.sleep(random.uniform(0.35, 0.7))

    coverage = len(priced_tickers) / max(1, len(universe))
    if len(priced_tickers) < 100 or coverage < MIN_COVERAGE:
        raise RuntimeError(
            f"{category} price coverage too low: {len(priced_tickers)}/{len(universe)} ({coverage:.1%}). "
            "Existing site data was not overwritten."
        )

    items = sorted(results.values(), key=lambda x: (-x["score"], x["symbol"]))
    for rank, item in enumerate(items, 1):
        item["rank"] = rank

    market_date = max((x["date"] for x in items if x.get("date")), default=None)
    payload_meta = {
        "app": "Morning Invest",
        "strategy": "MI_BB_HA_PSAR_TURNOVER_V7_LAZY_DATA",
        "category": category,
        "category_label": CATEGORY_LABEL[category],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market_date": market_date,
        "universe_source": universe_source,
        "restriction_snapshot": restriction_meta,
        "universe_count": len(universe),
        "priced_count": len(priced_tickers),
        "coverage_pct": round(coverage * 100, 1),
        "passed_count": len(items),
        "thresholds": thresholds,
        "filter_counts": dict(sorted(rejection.items())),
        "max_score": 2.7,
        "backtest_model": {
            "history": "3y_download_approx_2y_evaluation",
            "entry": "next_trading_day_open",
            "forward_sessions": [5, 10, 20],
            "cooldown_days": BACKTEST_COOLDOWN_DAYS,
            "historical_regulatory_status": "not_reconstructed"
        },
    }
    out_dir, detail_count = _write_category_site(category, payload_meta, items)
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
