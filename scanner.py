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
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MORNING_INVEST_COMPONENT_VERSION = "10.0"

import numpy as np
import pandas as pd
import yfinance as yf

from universe import Stock, fetch_kr_restricted_symbols, fetch_us_halted_symbols, get_universe

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------------------------------------------------------
# Morning Invest v10.0
# User strategy spec: 눌림목 / 돌파 스캐닝 로직 V3
# UI mapping: 싼게 좋아 = 눌림목(PB), 오르는게 좋아 = 돌파(BO)
# -----------------------------------------------------------------------------

FULL_HISTORY_CALENDAR_DAYS = 1200
BATCH_SIZE = 24
RETRY_BATCH_SIZE = 4
DOWNLOAD_THREADS = 4
PRIMARY_BATCH_SLEEP = (0.45, 0.80)
RETRY_BATCH_SLEEP = (1.2, 2.2)
RETRY_ATTEMPTS = 2
CHART_POINTS = 180

MIN_COVERAGE = {
    "KR": 0.95,
    "KR_ETF": 0.90,  # whitelist contains new alphanumeric KRX codes Yahoo may lag on
    "US": 0.95,
    "US_ETF": 0.95,
}

CATEGORY_DIR = {"KR": "kr", "KR_ETF": "kr-etf", "US": "us", "US_ETF": "us-etf"}
UNIVERSE_CACHE_FILE = {
    "KR": "universe_kr.json",
    "KR_ETF": "universe_kr_etf.json",
    "US": "universe_us.json",
    "US_ETF": "universe_us_etf.json",
}
CATEGORY_LABEL = {"KR": "국장", "KR_ETF": "국장 ETF", "US": "미장", "US_ETF": "미장 ETF"}
CATEGORY_TZ = {"KR": "Asia/Seoul", "KR_ETF": "Asia/Seoul", "US": "America/New_York", "US_ETF": "America/New_York"}
CATEGORY_CLOSE = {"KR": dtime(15, 40), "KR_ETF": dtime(15, 40), "US": dtime(16, 15), "US_ETF": dtime(16, 15)}
ETF_CATEGORIES = {"KR_ETF", "US_ETF"}
KR_CATEGORIES = {"KR", "KR_ETF"}
BENCHMARK_TICKER = {"KR": "^KS11", "KR_ETF": "^KS11", "US": "^GSPC", "US_ETF": "^GSPC"}

# V3 common rules
MIN_TRADING_DAYS = 280
MIN_AVG_TURNOVER_KRW_20D = 1_000_000_000.0
ENTRY_SCORE_THRESHOLD = 50.0
BACKTEST_LOOKBACK_DAYS = 252
BACKTEST_COOLDOWN_DAYS = 5
BACKTEST_RECENT_TRADES = 15
STRATEGY_PERCENTILE_DAYS = 250

# Price-limit guard for Korea. 29.5% avoids floating/adjustment noise around +30%.
KR_LIMIT_UP_GUARD = 0.295

# Market-cap is display-only in V3. Never use it as a hard filter.
STOCK_SHARES_CACHE_DAYS = 30
MARKET_SIZE_RETRY_ATTEMPTS = 2


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


def percentile_rank(value: float, reference) -> float:
    """Empirical CDF percentile in [0,1]. Ties count as <= by design."""
    v = finite(value)
    if not np.isfinite(v):
        return np.nan
    arr = np.asarray(reference, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return np.nan
    return float(np.mean(arr <= v))


def completed_daily(frame: pd.DataFrame, category: str) -> pd.DataFrame:
    """V3 always uses the last confirmed daily bar, even for manual QUICK runs."""
    if frame.empty:
        return frame
    now = datetime.now(ZoneInfo(CATEGORY_TZ[category]))
    out = frame
    if out.index[-1].date() == now.date() and now.time().replace(tzinfo=None) < CATEGORY_CLOSE[category]:
        out = out.iloc[:-1]
    return out


def _split_adjusted_volume(raw_volume: pd.Series, splits: pd.Series | None) -> pd.Series:
    vol = pd.to_numeric(raw_volume, errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
    if splits is None:
        return pd.Series(vol, index=raw_volume.index)
    sp = pd.to_numeric(splits, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    out = np.zeros_like(vol, dtype=float)
    future_factor = 1.0
    # Split on day i affects bars before i; day-i volume is already post-split shares.
    for i in range(len(vol) - 1, -1, -1):
        out[i] = vol[i] * future_factor
        ratio = sp[i]
        if np.isfinite(ratio) and ratio > 0 and not math.isclose(ratio, 1.0):
            future_factor *= ratio
    return pd.Series(out, index=raw_volume.index)


def normalize_market_frame(raw: pd.DataFrame, category: str) -> pd.DataFrame:
    """Create the V3 data streams.

    * Price indicators: corporate-action adjusted OHLC using Adj Close / raw Close.
    * Volume indicators: split-adjusted volume only.
    * Liquidity: unadjusted Close × unadjusted Volume (consistent raw approximation).
    """
    if raw is None or raw.empty:
        return pd.DataFrame()
    out = raw.copy()
    out.index = pd.DatetimeIndex(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out = out[~out.index.duplicated(keep="last")].sort_index()

    for c in ("Open", "High", "Low", "Close"):
        if c not in out.columns:
            return pd.DataFrame()
        out[c] = pd.to_numeric(out[c], errors="coerce")
    raw_close = out["Close"].copy()
    raw_open = out["Open"].copy()
    raw_high = out["High"].copy()
    raw_low = out["Low"].copy()
    raw_volume = pd.to_numeric(out.get("Volume", 0.0), errors="coerce").fillna(0.0).clip(lower=0.0)

    adj_close = pd.to_numeric(out.get("Adj Close", raw_close), errors="coerce")
    factor = (adj_close / raw_close.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(1.0)

    result = pd.DataFrame(index=out.index)
    result["Open"] = raw_open * factor
    result["High"] = raw_high * factor
    result["Low"] = raw_low * factor
    result["Close"] = raw_close * factor
    result["Volume"] = _split_adjusted_volume(raw_volume, out["Stock Splits"] if "Stock Splits" in out.columns else None)
    result["RawOpen"] = raw_open
    result["RawHigh"] = raw_high
    result["RawLow"] = raw_low
    result["RawClose"] = raw_close
    result["RawVolume"] = raw_volume
    result["RawTurnover"] = raw_close * raw_volume

    result = result.replace([np.inf, -np.inf], np.nan)
    result = result.dropna(subset=["Open", "High", "Low", "Close", "RawClose"])
    result = result[(result["Close"] > 0) & (result["High"] > 0) & (result["Low"] > 0)]
    result = completed_daily(result, category)
    return result


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    c = out["Close"]
    out["MA20"] = c.rolling(20).mean()
    out["MA50"] = c.rolling(50).mean()
    out["MA120"] = c.rolling(120).mean()

    prev = c.shift(1)
    tr = pd.concat(
        [(out["High"] - out["Low"]).abs(), (out["High"] - prev).abs(), (out["Low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    # Wilder ATR14.
    out["ATR14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    out["ATRP"] = out["ATR14"] / c.replace(0, np.nan)

    direction = np.sign(c.diff()).fillna(0.0)
    out["OBV"] = (direction * out["Volume"]).cumsum()
    out["AvgTurnover20"] = out["RawTurnover"].rolling(20).mean()
    return out


def u_eligible_series(ind: pd.DataFrame, category: str, liquidity_native: float) -> pd.Series:
    n = len(ind)
    enough = pd.Series(np.arange(n) >= (MIN_TRADING_DAYS - 1), index=ind.index)
    liquid = pd.to_numeric(ind["AvgTurnover20"], errors="coerce") >= liquidity_native
    trading = pd.to_numeric(ind["RawVolume"], errors="coerce").fillna(0.0) > 0
    if category in KR_CATEGORIES:
        ret = pd.to_numeric(ind["RawClose"], errors="coerce").pct_change()
        not_limit_up = ret < KR_LIMIT_UP_GUARD
        not_limit_up.iloc[0] = True
    else:
        not_limit_up = pd.Series(True, index=ind.index)
    return enough & liquid & trading & not_limit_up


def close_location(ind: pd.DataFrame, t: int) -> float:
    h, l, c = (finite(ind["High"].iloc[t]), finite(ind["Low"].iloc[t]), finite(ind["Close"].iloc[t]))
    if not all(np.isfinite(x) for x in (h, l, c)) or h <= l:
        return np.nan
    return (c - l) / (h - l)


def market_regime_series(benchmark: pd.DataFrame) -> pd.Series:
    c = pd.to_numeric(benchmark["Close"], errors="coerce")
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    m = pd.Series(0.60, index=benchmark.index, dtype=float)
    m.loc[c > ma20] = 0.85
    m.loc[(c > ma20) & (ma20 > ma60)] = 1.00
    return m


def _aligned_value(series: pd.Series, date) -> float:
    if series is None or series.empty:
        return np.nan
    ts = pd.Timestamp(date)
    try:
        if ts in series.index:
            return finite(series.loc[ts])
        v = series.reindex(series.index.union([ts])).sort_index().ffill().loc[ts]
        return finite(v)
    except Exception:
        return np.nan


# ------------------------- V3 눌림목 / 싼게 좋아 -------------------------

def eval_pullback(ind: pd.DataFrame, t: int, rs_percentile: float, M: float) -> dict:
    gates: dict[str, bool] = {}
    if t < MIN_TRADING_DAYS - 1 or t - 60 < 0 or t - 126 < 0:
        return {"gate_pass": False, "eligible": False, "reason": "history", "gates": gates}

    c = finite(ind["Close"].iloc[t])
    ma20 = finite(ind["MA20"].iloc[t])
    ma50 = finite(ind["MA50"].iloc[t])
    atr = finite(ind["ATR14"].iloc[t])
    if not all(np.isfinite(x) for x in (c, ma20, ma50, atr)) or c <= 0 or atr <= 0:
        return {"gate_pass": False, "eligible": False, "reason": "indicator", "gates": gates}

    # i_H = argmax High[t-20 ... t-3]
    hs = pd.to_numeric(ind["High"].iloc[t - 20 : t - 2], errors="coerce")
    if len(hs) != 18 or hs.isna().all():
        return {"gate_pass": False, "eligible": False, "reason": "swing", "gates": gates}
    i_H = int(ind.index.get_loc(hs.idxmax()))
    H_sw = finite(ind["High"].iloc[i_H])
    pullback_days = t - i_H

    prior_below = []
    for i in range(i_H - 1, -1, -1):
        ci, m20i = finite(ind["Close"].iloc[i]), finite(ind["MA20"].iloc[i])
        if np.isfinite(ci) and np.isfinite(m20i) and ci < m20i:
            prior_below.append(i)
            break
    if prior_below:
        i_L = prior_below[0] + 1
    else:
        i_L = i_H - 40
    if i_L < 0 or i_H - 60 < 0:
        return {"gate_pass": False, "eligible": False, "reason": "negative_index_guard", "gates": gates}

    L_base = finite(pd.to_numeric(ind["Low"].iloc[i_L : i_H + 1], errors="coerce").min())
    if not np.isfinite(H_sw) or not np.isfinite(L_base) or H_sw <= L_base or i_H - i_L < 5:
        return {"gate_pass": False, "eligible": False, "reason": "leg_invalid", "gates": gates}

    swing_rise = (H_sw - L_base) / L_base
    r = (H_sw - c) / (H_sw - L_base)

    impulse_vol = finite(pd.to_numeric(ind["Volume"].iloc[i_L : i_H + 1], errors="coerce").mean())
    pullback_vol = finite(pd.to_numeric(ind["Volume"].iloc[i_H + 1 : t], errors="coerce").mean())
    current_vol = finite(ind["Volume"].iloc[t])
    dry_vol = pullback_vol / impulse_vol if impulse_vol > 0 and np.isfinite(pullback_vol) else np.nan
    recovery_vol = current_vol / pullback_vol if pullback_vol > 0 else np.nan
    cl = close_location(ind, t)

    pb_low = finite(pd.to_numeric(ind["Low"].iloc[i_H + 1 : t + 1], errors="coerce").min())
    stop = max(pb_low - 0.5 * atr, c * 0.88) if np.isfinite(pb_low) else np.nan
    est_risk = (c - stop) / c if np.isfinite(stop) and c > 0 else np.nan

    prior_60_high = finite(pd.to_numeric(ind["High"].iloc[i_H - 60 : i_H], errors="coerce").max())
    gates = {
        "G1_MA20_gt_MA50": bool(ma20 > ma50),
        "G2_C_ge_MA50x093": bool(c >= ma50 * 0.93),
        "G3_MA50_rising_20d": bool(np.isfinite(finite(ind["MA50"].iloc[t - 20])) and ma50 > finite(ind["MA50"].iloc[t - 20])),
        "G4_SwingRise_ge_15pct": bool(np.isfinite(swing_rise) and swing_rise >= 0.15),
        "G5_real_swing_high": bool(np.isfinite(prior_60_high) and H_sw >= prior_60_high),
        "G6_r_020_to_065": bool(np.isfinite(r) and 0.20 <= r <= 0.65),
        "G7_DryVol_le_085": bool(np.isfinite(dry_vol) and dry_vol <= 0.85),
        "G8_RecoveryVol_ge_120": bool(np.isfinite(recovery_vol) and recovery_vol >= 1.20),
        "G9_CloseLocation_ge_040": bool(np.isfinite(cl) and cl >= 0.40),
        "G10_EstRisk_le_12pct": bool(np.isfinite(est_risk) and 0 < est_risk <= 0.12),
    }
    gate_pass = all(gates.values())
    if not gate_pass:
        return {
            "gate_pass": False, "eligible": False, "gates": gates,
            "metrics": {
                "i_H": i_H, "i_L": i_L, "H_sw": clean(H_sw), "L_base": clean(L_base),
                "SwingRise": clean(swing_rise, 6), "r": clean(r, 6), "PullbackDays": pullback_days,
                "DryVol": clean(dry_vol, 6), "RecoveryVol": clean(recovery_vol, 6),
                "CloseLocation": clean(cl, 6), "StopPrice": clean(stop), "EstimatedRisk": clean(est_risk, 6),
            },
        }

    s1 = 100.0 * math.exp(-((r - 0.45) ** 2) / (2 * (0.15 ** 2)))
    s2 = 100.0 * clip((0.95 - dry_vol) / 0.35, 0.0, 1.0)
    s3 = 100.0 * clip((recovery_vol - 1.10) / 0.90, 0.0, 1.0)
    s4 = 100.0 * clip((cl - 0.40) / 0.50, 0.0, 1.0)
    ma50_10 = finite(ind["MA50"].iloc[t - 10])
    g50 = (ma50 - ma50_10) / (ma50_10 * 10.0) if np.isfinite(ma50_10) and ma50_10 > 0 else np.nan
    s5 = 100.0 * clip(g50 / 0.0025, 0.0, 1.0) if np.isfinite(g50) else 0.0
    s6 = 100.0 * math.exp(-((pullback_days - 9) ** 2) / (2 * (5.0 ** 2)))
    rs = finite(rs_percentile)
    s7 = clip((rs - 40.0) / 0.6, 0.0, 100.0) if np.isfinite(rs) else 0.0

    scores = {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "S5": s5, "S6": s6, "S7": s7}
    weights = {"S1": 0.24, "S2": 0.18, "S3": 0.10, "S4": 0.08, "S5": 0.12, "S6": 0.08, "S7": 0.20}
    raw_score = sum(weights[k] * scores[k] for k in scores)
    final_score = raw_score * M

    # Diagnostic RS_swing; true cross-sectional RS_swing is filled by caller only when available.
    metrics = {
        "i_H": i_H, "i_L": i_L, "H_sw": clean(H_sw), "L_base": clean(L_base),
        "SwingRise": clean(swing_rise, 6), "r": clean(r, 6), "PullbackDays": pullback_days,
        "DryVol": clean(dry_vol, 6), "RecoveryVol": clean(recovery_vol, 6), "CloseLocation": clean(cl, 6),
        "MA20": clean(ma20), "MA50": clean(ma50), "MA50_slope": clean(g50, 8),
        "RS_percentile": clean(rs, 2), "ATR14": clean(atr), "StopPrice": clean(stop),
        "EstimatedRisk": clean(est_risk, 6), "StopATR": clean((c - stop) / atr, 4),
    }
    contributions = {k: weights[k] * scores[k] for k in scores}
    return {
        "gate_pass": True,
        "eligible": bool(final_score >= ENTRY_SCORE_THRESHOLD),
        "adopted": False,
        "raw_score": round(raw_score, 4),
        "M": round(M, 2),
        "score": round(final_score, 4),
        "scores": {k: round(v, 3) for k, v in scores.items()},
        "weights": weights,
        "contributions": {k: round(v, 3) for k, v in contributions.items()},
        "gates": gates,
        "metrics": metrics,
        "stop": stop,
    }


# --------------------------- V3 돌파 / 오르는게 좋아 ---------------------------

def supply_profile(ind: pd.DataFrame, t: int) -> tuple[float, float, float]:
    c = finite(ind["Close"].iloc[t])
    if not np.isfinite(c) or c <= 0 or t - 120 < 0:
        return np.nan, np.nan, np.nan

    edges = c * np.power(1.01, np.arange(-25, 26, dtype=float))  # 51 edges -> 50 bins
    centers = np.sqrt(edges[:-1] * edges[1:])
    bins = np.zeros(50, dtype=float)

    for i in range(t - 120, t):
        h = finite(ind["High"].iloc[i])
        l = finite(ind["Low"].iloc[i])
        close_i = finite(ind["Close"].iloc[i])
        v = finite(ind["Volume"].iloc[i], 0.0)
        if not all(np.isfinite(x) for x in (h, l, close_i, v)) or v <= 0:
            continue
        tv = v * (h + l + close_i) / 3.0
        weight = 0.5 ** ((t - i) / 45.0)
        tv *= weight

        if h <= l:
            tp = (h + l + close_i) / 3.0
            j = int(np.searchsorted(edges, tp, side="right") - 1)
            if 0 <= j < 50:
                bins[j] += tv
            continue

        span = h - l
        left = np.maximum(l, edges[:-1])
        right = np.minimum(h, edges[1:])
        overlap = np.maximum(0.0, right - left)
        bins += tv * (overlap / span)

    overhead_mask = (centers > c) & (centers <= 1.20 * c)
    nearby_mask = (centers >= 0.80 * c) & (centers <= 1.20 * c)
    overhead = float(bins[overhead_mask].sum())
    nearby = float(bins[nearby_mask].sum())
    ratio = overhead / nearby if nearby > 0 else 0.0
    return ratio, overhead, nearby


def eval_breakout(ind: pd.DataFrame, t: int, rs_percentile: float, M: float) -> dict:
    gates: dict[str, bool] = {}
    if t < MIN_TRADING_DAYS - 1 or t - 250 < 0 or t - 120 < 0 or t - 63 < 0:
        return {"gate_pass": False, "eligible": False, "reason": "history", "gates": gates}

    c = finite(ind["Close"].iloc[t])
    ma20 = finite(ind["MA20"].iloc[t])
    ma50 = finite(ind["MA50"].iloc[t])
    ma120 = finite(ind["MA120"].iloc[t])
    atr = finite(ind["ATR14"].iloc[t])
    cl = close_location(ind, t)
    if not all(np.isfinite(x) for x in (c, ma20, ma50, ma120, atr, cl)) or c <= 0 or atr <= 0:
        return {"gate_pass": False, "eligible": False, "reason": "indicator", "gates": gates}

    breakout_level = finite(pd.to_numeric(ind["High"].iloc[t - 20 : t], errors="coerce").max())
    v60 = finite(pd.to_numeric(ind["Volume"].iloc[t - 60 : t], errors="coerce").mean())
    vol_t = finite(ind["Volume"].iloc[t])
    m_vol = vol_t / v60 if v60 > 0 else np.nan
    ret1 = c / finite(ind["Close"].iloc[t - 1]) - 1.0 if finite(ind["Close"].iloc[t - 1]) > 0 else np.nan
    stop = breakout_level - atr if np.isfinite(breakout_level) else np.nan
    est_risk = (c - stop) / c if np.isfinite(stop) and c > 0 else np.nan

    gates = {
        "G1_breakout_20d_x1005": bool(np.isfinite(breakout_level) and c > breakout_level * 1.005),
        "G2_volume_ge_1_5x": bool(np.isfinite(m_vol) and m_vol >= 1.50),
        "G3_CloseLocation_ge_065": bool(cl >= 0.65),
        "G4_C_gt_MA50_and_MA120": bool(c > ma50 and c > ma120),
        "G5_C_over_MA20_le_130": bool(c / ma20 <= 1.30),
        "G6_day_return_le_15pct": bool(np.isfinite(ret1) and ret1 <= 0.15),
        "G7_EstRisk_le_12pct": bool(np.isfinite(est_risk) and 0 < est_risk <= 0.12),
    }
    gate_pass = all(gates.values())
    if not gate_pass:
        return {
            "gate_pass": False, "eligible": False, "gates": gates,
            "metrics": {
                "BreakoutLevel20": clean(breakout_level), "m_vol": clean(m_vol, 5),
                "CloseLocation": clean(cl, 6), "StopPrice": clean(stop), "EstimatedRisk": clean(est_risk, 6),
            },
        }

    supply_ratio, overhead, nearby = supply_profile(ind, t)
    if not np.isfinite(supply_ratio):
        supply_ratio = 0.0
    s1 = 100.0 * clip(1.0 - supply_ratio / 0.50, 0.0, 1.0)
    s2 = 100.0 * clip(math.log(m_vol / 1.5) / math.log(4.0 / 1.5), 0.0, 1.0)

    vcp_v = finite(pd.to_numeric(ind["ATRP"].iloc[t - 5 : t], errors="coerce").mean())
    q = percentile_rank(vcp_v, pd.to_numeric(ind["ATRP"].iloc[t - 250 : t], errors="coerce").to_numpy(dtype=float))
    s3 = 100.0 * clip((0.45 - q) / 0.40, 0.0, 1.0) if np.isfinite(q) else 0.0

    obv_pct = percentile_rank(finite(ind["OBV"].iloc[t - 1]), pd.to_numeric(ind["OBV"].iloc[t - 60 : t], errors="coerce").to_numpy(dtype=float))
    price_pct = percentile_rank(finite(ind["Close"].iloc[t - 1]), pd.to_numeric(ind["Close"].iloc[t - 60 : t], errors="coerce").to_numpy(dtype=float))
    div = obv_pct - price_pct if np.isfinite(obv_pct) and np.isfinite(price_pct) else np.nan
    s4 = 100.0 * clip((div + 0.10) / 0.40, 0.0, 1.0) if np.isfinite(div) else 0.0

    rs = finite(rs_percentile)
    s5 = clip((rs - 50.0) * 2.0, 0.0, 100.0) if np.isfinite(rs) else 0.0
    s6 = 100.0 * clip((cl - 0.65) / 0.30, 0.0, 1.0)

    scores = {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "S5": s5, "S6": s6}
    weights = {"S1": 0.25, "S2": 0.20, "S3": 0.20, "S4": 0.15, "S5": 0.15, "S6": 0.05}
    raw_score = sum(weights[k] * scores[k] for k in scores)
    final_score = raw_score * M

    raw_tv = pd.to_numeric(ind["RawTurnover"], errors="coerce")
    prior_tv = finite(raw_tv.iloc[t - 60 : t].mean())
    m_tv = finite(raw_tv.iloc[t]) / prior_tv if prior_tv > 0 else np.nan
    breakout_pct = c / breakout_level - 1.0 if breakout_level > 0 else np.nan
    high52 = finite(pd.to_numeric(ind["High"].iloc[max(0, t - 252) : t + 1], errors="coerce").max())
    tier = "52W" if np.isfinite(high52) and c >= high52 else "20D"

    metrics = {
        "BreakoutLevel20": clean(breakout_level), "BreakoutPct": clean(breakout_pct, 6), "BreakoutTier": tier,
        "m_vol": clean(m_vol, 6), "m_tv": clean(m_tv, 6), "ATRP_percentile": clean(q, 6),
        "SupplyRatio": clean(supply_ratio, 6), "Overhead": clean(overhead, 0), "Nearby": clean(nearby, 0),
        "OBV_Div": clean(div, 6), "obv_pct": clean(obv_pct, 6), "price_pct": clean(price_pct, 6),
        "RS_percentile": clean(rs, 2), "CloseLocation": clean(cl, 6),
        "MA50_over_MA120": clean(ma50 / ma120 - 1.0, 6) if ma120 > 0 else None,
        "ATR14": clean(atr), "StopPrice": clean(stop), "EstimatedRisk": clean(est_risk, 6),
        "StopATR": clean((c - stop) / atr, 4),
    }
    contributions = {k: weights[k] * scores[k] for k in scores}
    return {
        "gate_pass": True,
        "eligible": bool(final_score >= ENTRY_SCORE_THRESHOLD),
        "adopted": False,
        "raw_score": round(raw_score, 4),
        "M": round(M, 2),
        "score": round(final_score, 4),
        "scores": {k: round(v, 3) for k, v in scores.items()},
        "weights": weights,
        "contributions": {k: round(v, 3) for k, v in contributions.items()},
        "gates": gates,
        "metrics": metrics,
        "stop": stop,
    }


def adopt_exclusive(pb: dict, bo: dict) -> tuple[dict, dict]:
    pb = dict(pb or {})
    bo = dict(bo or {})
    pb["adopted"] = False
    bo["adopted"] = False
    pe, be = bool(pb.get("eligible")), bool(bo.get("eligible"))
    if pe and be:
        if finite(pb.get("score"), -np.inf) >= finite(bo.get("score"), -np.inf):
            pb["adopted"] = True
        else:
            bo["adopted"] = True
    elif pe:
        pb["adopted"] = True
    elif be:
        bo["adopted"] = True
    return pb, bo


def build_cross_sectional_rs(indicators: dict[str, pd.DataFrame], eligible: dict[str, pd.Series], period: int) -> pd.DataFrame:
    returns = {}
    masks = {}
    for ticker, ind in indicators.items():
        returns[ticker] = pd.to_numeric(ind["Close"], errors="coerce").pct_change(period)
        masks[ticker] = eligible[ticker]
    if not returns:
        return pd.DataFrame()
    r = pd.concat(returns, axis=1)
    m = pd.concat(masks, axis=1).reindex(r.index).fillna(False)
    return r.where(m).rank(axis=1, method="average", pct=True) * 100.0


def current_rs_swing_percentiles(indicators: dict[str, pd.DataFrame], current_u: dict[str, bool]) -> dict[str, float]:
    """Diagnostic RS_swing from the V3 spec.

    RS_swing return = H_sw / C[t-126] - 1, where H_sw is the same pullback
    swing high searched over t-20..t-3.  Percentile is cross-sectional across
    the current U-filter-passed universe.  It does not affect selection.
    """
    raw: dict[str, float] = {}
    for ticker, ind in indicators.items():
        if not current_u.get(ticker) or len(ind) < MIN_TRADING_DAYS:
            continue
        t = len(ind) - 1
        if t - 126 < 0 or t - 20 < 0:
            continue
        hs = pd.to_numeric(ind["High"].iloc[t - 20 : t - 2], errors="coerce")
        c126 = finite(ind["Close"].iloc[t - 126])
        if hs.empty or hs.isna().all() or not np.isfinite(c126) or c126 <= 0:
            continue
        h_sw = finite(hs.max())
        if np.isfinite(h_sw):
            raw[ticker] = h_sw / c126 - 1.0

    if not raw:
        return {}
    values = np.asarray(list(raw.values()), dtype=float)
    return {ticker: percentile_rank(value, values) * 100.0 for ticker, value in raw.items()}


def benchmark_return(bench: pd.DataFrame, entry_date, end_date) -> float:
    if bench.empty:
        return np.nan
    try:
        entry_ts = pd.Timestamp(entry_date)
        end_ts = pd.Timestamp(end_date)
        if entry_ts not in bench.index or end_ts not in bench.index:
            return np.nan
        o = finite(bench.loc[entry_ts, "Open"])
        c = finite(bench.loc[end_ts, "Close"])
        return c / o - 1.0 if o > 0 and np.isfinite(c) else np.nan
    except Exception:
        return np.nan


def summarize_trades(trades: list[dict]) -> dict:
    def vals(key):
        return [finite(x.get(key)) for x in trades if np.isfinite(finite(x.get(key)))]
    def avg(key):
        a = vals(key)
        return float(np.mean(a)) if a else np.nan
    def med(key):
        a = vals(key)
        return float(np.median(a)) if a else np.nan
    def win(key):
        a = vals(key)
        return float(np.mean(np.asarray(a) > 0)) if a else np.nan

    return {
        "available": bool(trades),
        "signals": len(trades),
        "avg_5d": clean(avg("FwdRet5"), 6),
        "avg_10d": clean(avg("FwdRet10"), 6),
        "avg_20d": clean(avg("FwdRet20"), 6),
        "median_20d": clean(med("FwdRet20"), 6),
        "avg_ex_5d": clean(avg("ExRet5"), 6),
        "avg_ex_10d": clean(avg("ExRet10"), 6),
        "avg_ex_20d": clean(avg("ExRet20"), 6),
        "median_ex_20d": clean(med("ExRet20"), 6),
        "win_5d": clean(win("ExRet5"), 6),
        "win_10d": clean(win("ExRet10"), 6),
        "win_20d": clean(win("ExRet20"), 6),
        "avg_mfe_20d": clean(avg("MFE20"), 6),
        "avg_mae_20d": clean(avg("MAE20"), 6),
        "avg_trade_ret": clean(avg("TradeRet"), 6),
        "trades": trades[-BACKTEST_RECENT_TRADES:],
        "validation_basis": "index_excess_return",
    }


def backtest_both(
    ind: pd.DataFrame,
    category: str,
    u_series: pd.Series,
    rs126: pd.Series,
    rs63: pd.Series,
    regime: pd.Series,
    benchmark: pd.DataFrame,
) -> tuple[dict, dict, list[float], list[float]]:
    """Point-in-time V3 event backtest for both strategies.

    Implements E1, P2, P3 and X1-X3. P1/P4/P6 require live portfolio/sector state
    and therefore remain execution-layer constraints rather than single-stock metrics.
    """
    pb_trades: list[dict] = []
    bo_trades: list[dict] = []
    pb_score_pool: list[float] = []
    bo_score_pool: list[float] = []
    n = len(ind)
    if n < MIN_TRADING_DAYS + 21:
        return summarize_trades([]), summarize_trades([]), pb_score_pool, bo_score_pool

    last_signal_t: int | None = None
    end_t = n - 21  # t+20 must exist for a completed backtest trade
    start_t = max(MIN_TRADING_DAYS - 1, end_t - BACKTEST_LOOKBACK_DAYS + 1)
    # StrategyPercentile uses the most recent 250 trading sessions, including
    # dates too recent to have a completed 20D forward return.
    hist_pool_start = max(MIN_TRADING_DAYS - 1, n - STRATEGY_PERCENTILE_DAYS)

    for t in range(hist_pool_start, n):
        if not bool(u_series.iloc[t]):
            continue
        date = ind.index[t]
        M = _aligned_value(regime, date)
        if not np.isfinite(M):
            continue
        pb = eval_pullback(ind, t, finite(rs126.reindex(ind.index).iloc[t]), M)
        bo = eval_breakout(ind, t, finite(rs63.reindex(ind.index).iloc[t]), M)
        if pb.get("gate_pass"):
            pb_score_pool.append(float(pb.get("score", 0.0)))
        if bo.get("gate_pass"):
            bo_score_pool.append(float(bo.get("score", 0.0)))

        if t < start_t or t > end_t:
            continue
        pb, bo = adopt_exclusive(pb, bo)
        chosen = ("cheap", pb) if pb.get("adopted") else (("rising", bo) if bo.get("adopted") else None)
        if chosen is None:
            continue
        if last_signal_t is not None and t - last_signal_t <= BACKTEST_COOLDOWN_DAYS:
            continue

        mode, signal = chosen
        entry_pos = t + 1
        entry = finite(ind["Open"].iloc[entry_pos])
        entry_vol = finite(ind["Volume"].iloc[entry_pos], 0.0)
        close_t = finite(ind["Close"].iloc[t])
        stop = finite(signal.get("stop"))
        if not np.isfinite(entry) or entry <= 0 or entry_vol <= 0:  # X1
            continue
        gap = entry / close_t - 1.0 if close_t > 0 else np.nan
        if np.isfinite(gap) and gap >= 0.29:  # X2
            continue
        if not np.isfinite(stop) or entry <= stop:  # X3
            continue

        actual_risk = (entry - stop) / entry
        horizons = {}
        for h in (5, 10, 20):
            end_pos = t + h
            end_close = finite(ind["Close"].iloc[end_pos])
            fwd = end_close / entry - 1.0 if entry > 0 and np.isfinite(end_close) else np.nan
            bret = benchmark_return(benchmark, ind.index[entry_pos], ind.index[end_pos])
            horizons[f"FwdRet{h}"] = fwd
            horizons[f"ExRet{h}"] = fwd - bret if np.isfinite(fwd) and np.isfinite(bret) else np.nan

        lows = pd.to_numeric(ind["Low"].iloc[t + 1 : t + 21], errors="coerce")
        highs = pd.to_numeric(ind["High"].iloc[t + 1 : t + 21], errors="coerce")
        mae = finite(lows.min()) / entry - 1.0
        mfe = finite(highs.max()) / entry - 1.0

        stop_hit = False
        stop_date = None
        for j in range(t + 1, t + 21):
            if finite(ind["Low"].iloc[j]) <= stop:
                stop_hit = True
                stop_date = ind.index[j]
                break
        trade_ret = stop / entry - 1.0 if stop_hit else finite(ind["Close"].iloc[t + 20]) / entry - 1.0

        trade = {
            "signal_date": pd.Timestamp(date).date().isoformat(),
            "EntryDate": pd.Timestamp(ind.index[entry_pos]).date().isoformat(),
            "EntryPrice": clean(entry),
            "score": clean(signal.get("score"), 3),
            "RawScore": clean(signal.get("raw_score"), 3),
            "M": clean(signal.get("M"), 2),
            "GapPct": clean(gap, 6),
            "ActualRisk": clean(actual_risk, 6),
            **{k: clean(v, 6) for k, v in horizons.items()},
            "MAE20": clean(mae, 6),
            "MFE20": clean(mfe, 6),
            "TradeRet": clean(trade_ret, 6),
            "StopPrice": clean(stop),
            "StopHit": stop_hit,
            "StopDate": pd.Timestamp(stop_date).date().isoformat() if stop_date is not None else None,
        }
        (pb_trades if mode == "cheap" else bo_trades).append(trade)
        last_signal_t = t

    return summarize_trades(pb_trades), summarize_trades(bo_trades), pb_score_pool, bo_score_pool


def _make_chart(ind: pd.DataFrame) -> dict:
    chart = ind.tail(CHART_POINTS)
    return {
        "d": [pd.Timestamp(i).date().isoformat() for i in chart.index],
        "c": [clean(v) for v in chart["Close"]],
        "ma20": [clean(v) for v in chart["MA20"]],
        "ma50": [clean(v) for v in chart["MA50"]],
        "ma120": [clean(v) for v in chart["MA120"]],
    }


def fetch_usdkrw() -> float:
    try:
        raw = yf.download("KRW=X", period="10d", interval="1d", auto_adjust=False, progress=False, threads=False, timeout=25)
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw.xs("Close", axis=1, level=0).iloc[:, 0]
        else:
            close = raw["Close"]
        fx = finite(pd.to_numeric(close, errors="coerce").dropna().iloc[-1])
        if 500 <= fx <= 3000:
            return fx
    except Exception as exc:
        print(f"USD/KRW fetch failed: {exc}")

    for folder in ("us", "us-etf"):
        path = DATA_DIR / folder / "summary.json"
        if path.is_file():
            try:
                fx = finite((json.loads(path.read_text(encoding="utf-8")).get("thresholds") or {}).get("usdkrw"))
                if 500 <= fx <= 3000:
                    return fx
            except Exception:
                pass
    raise RuntimeError("USD/KRW unavailable and no previous valid FX snapshot exists")


def liquidity_threshold_native(category: str, usdkrw: float | None) -> float:
    if category in KR_CATEGORIES:
        return MIN_AVG_TURNOVER_KRW_20D
    if not usdkrw or usdkrw <= 0:
        raise RuntimeError("USD/KRW required for US liquidity threshold")
    return MIN_AVG_TURNOVER_KRW_20D / usdkrw


def _size_cache_path(category: str) -> Path:
    return DATA_DIR / CATEGORY_DIR[category] / "sizes.json"


def _load_size_cache(category: str) -> dict:
    path = _size_cache_path(category)
    if not path.is_file():
        return {}
    try:
        p = json.loads(path.read_text(encoding="utf-8"))
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def _cache_age_days(entry: dict) -> float:
    try:
        dt = datetime.fromisoformat(str(entry.get("fetched_at", "")).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    except Exception:
        return 9999.0


def resolve_market_size_display(stock: Stock, close: float, usdkrw: float | None, cache: dict) -> tuple[float, str] | tuple[None, str]:
    if stock.category in ETF_CATEGORIES:
        return None, "etf_not_queried"
    entry = cache.get(stock.ticker)
    if not isinstance(entry, dict) or _cache_age_days(entry) > STOCK_SHARES_CACHE_DAYS:
        entry = None
        for attempt in range(MARKET_SIZE_RETRY_ATTEMPTS):
            try:
                t = yf.Ticker(stock.ticker)
                shares = finite(t.fast_info["shares"])
                if np.isfinite(shares) and shares > 0:
                    entry = {"basis": "shares", "value": shares, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                else:
                    mc = finite(t.fast_info["market_cap"])
                    if np.isfinite(mc) and mc > 0:
                        entry = {"basis": "market_cap", "value": mc, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                if entry:
                    cache[stock.ticker] = entry
                    break
            except Exception:
                time.sleep(0.7 * (attempt + 1))
    if not isinstance(entry, dict):
        return None, "unavailable"
    value = finite(entry.get("value"))
    basis = str(entry.get("basis") or "")
    if not np.isfinite(value) or value <= 0:
        return None, "unavailable"
    native = value * close if basis == "shares" else value
    if stock.currency == "KRW":
        return float(native), basis
    if usdkrw and usdkrw > 0:
        return float(native * usdkrw), basis
    return None, "unavailable"


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


def download_batch(tickers: list[str], timeout=45) -> pd.DataFrame:
    end = datetime.now(timezone.utc).date() + timedelta(days=1)
    start = end - timedelta(days=FULL_HISTORY_CALENDAR_DAYS)
    return yf.download(
        tickers=tickers,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        actions=True,
        progress=False,
        threads=min(DOWNLOAD_THREADS, max(1, len(tickers))),
        timeout=timeout,
        multi_level_index=True,
    )


def download_benchmark(category: str) -> pd.DataFrame:
    ticker = BENCHMARK_TICKER[category]
    raw = download_batch([ticker], timeout=45)
    frame = frame_for(raw, ticker)
    norm = normalize_market_frame(frame, category)
    if len(norm) < MIN_TRADING_DAYS:
        raise RuntimeError(f"benchmark {ticker} history unavailable ({len(norm)} rows)")
    return norm


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _load_restrictions(category: str, universe: list[Stock]):
    if category == "KR":
        return fetch_kr_restricted_symbols(universe)
    if category == "KR_ETF":
        return set(), {"source": "ETF_WHITELIST_PLUS_ZERO_VOLUME_HALT_GUARD", "restricted_count": 0}
    halted, meta = fetch_us_halted_symbols()
    return halted, meta


def _detail_filename(item: dict) -> str:
    symbol = re.sub(r"[^A-Za-z0-9._-]+", "_", str(item.get("symbol") or "stock")).strip("._-")
    symbol = (symbol or "stock")[:36]
    digest = hashlib.sha1(str(item["ticker"]).encode("utf-8")).hexdigest()[:12]
    return f"{symbol}-{digest}.json"


def _compact_backtest(bt: dict) -> dict:
    return {
        "available": bool((bt or {}).get("available")),
        "signals": (bt or {}).get("signals"),
        "avg_20d": (bt or {}).get("avg_20d"),
        "avg_ex_20d": (bt or {}).get("avg_ex_20d"),
        "win_20d": (bt or {}).get("win_20d"),
    }


def _summary_item(item: dict, detail_path: str) -> dict:
    rising = item.get("rising") or {}
    return {
        "ticker": item["ticker"], "symbol": item["symbol"], "name": item["name"],
        "category": item["category"], "exchange": item["exchange"], "currency": item["currency"],
        "date": item["date"], "close": item["close"], "day_change_pct": item["day_change_pct"],
        "market_size_krw": (item.get("metrics") or {}).get("market_size_krw"),
        "rank": item.get("rank"), "eligible": bool(item.get("eligible")), "score": item.get("score", 0.0),
        "raw_score": item.get("raw_score"), "M": item.get("M"), "backtest": _compact_backtest(item.get("backtest") or {}),
        "rising": {
            "rank": rising.get("rank"), "eligible": bool(rising.get("eligible")), "score": rising.get("score", 0.0),
            "raw_score": rising.get("raw_score"), "M": rising.get("M"), "backtest": _compact_backtest(rising.get("backtest") or {}),
        },
        "detail_path": detail_path,
    }


def _write_category_site(category: str, payload_meta: dict, items: list[dict], size_cache: dict, scan_mode: str) -> tuple[Path, int]:
    category_dir = DATA_DIR / CATEGORY_DIR[category]
    previous_details: dict[str, dict] = {}
    if scan_mode == "QUICK":
        old = category_dir / "stocks"
        if old.is_dir():
            for p in old.glob("*.json"):
                try:
                    j = json.loads(p.read_text(encoding="utf-8"))
                    if j.get("ticker"):
                        previous_details[str(j["ticker"])] = j
                except Exception:
                    pass

    shutil.rmtree(category_dir, ignore_errors=True)
    stocks_dir = category_dir / "stocks"
    stocks_dir.mkdir(parents=True, exist_ok=True)
    summary_items = []

    for item in items:
        if scan_mode == "QUICK":
            prev = previous_details.get(str(item.get("ticker"))) or {}
            if prev.get("backtest"):
                item["backtest"] = prev["backtest"]
            if (prev.get("rising") or {}).get("backtest"):
                item.setdefault("rising", {})["backtest"] = prev["rising"]["backtest"]
        filename = _detail_filename(item)
        rel = f"data/{CATEGORY_DIR[category]}/stocks/{filename}"
        summary_items.append(_summary_item(item, rel))
        (stocks_dir / filename).write_text(json.dumps(item, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    summary_payload = {**payload_meta, "storage_model": "summary_plus_lazy_stock_detail_v10", "detail_count": len(items), "items": summary_items}
    summary_file = category_dir / "summary.json"
    summary_file.write_text(json.dumps(summary_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    sizes_file = category_dir / "sizes.json"
    sizes_file.write_text(json.dumps(size_cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    universe_snapshot = category_dir / "universe.json"
    root_cache = DATA_DIR / UNIVERSE_CACHE_FILE[category]
    if root_cache.is_file():
        shutil.copy2(root_cache, universe_snapshot)

    bundle = category_dir / "bundle.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(summary_file, "summary.json")
        zf.write(sizes_file, "sizes.json")
        if universe_snapshot.is_file():
            zf.write(universe_snapshot, "universe.json")
        for p in stocks_dir.glob("*.json"):
            zf.write(p, f"stocks/{p.name}")
    return category_dir, len(items)


def scan_category(category: str, usdkrw: float | None, scan_mode: str = "FULL") -> None:
    universe, universe_source = get_universe(category)
    restricted, restriction_meta = _load_restrictions(category, universe)
    liquidity_native = liquidity_threshold_native(category, usdkrw)
    size_cache = _load_size_cache(category)
    by_ticker = {s.ticker: s for s in universe}

    # Normalize US halt symbols to Yahoo tickers.
    restricted_norm = {str(x).replace(".", "-") for x in restricted}
    scan_universe = [s for s in universe if s.ticker not in restricted_norm and s.symbol not in restricted]

    print("=" * 78)
    print(f"Morning Invest v10.0 V3 | {category} | {scan_mode} | universe={len(universe):,} | scan={len(scan_universe):,}")
    print(f"ETF whitelist={'ON' if category in ETF_CATEGORIES else 'N/A'} | U2 20D avg turnover >= KRW 1.0B")
    print("싼게 좋아=눌림목 V3 | 오르는게 좋아=돌파 V3 | confirmed daily bars only")
    print("=" * 78)

    benchmark = download_benchmark(category)
    regime = market_regime_series(benchmark)

    frames: dict[str, pd.DataFrame] = {}
    indicators: dict[str, pd.DataFrame] = {}
    eligible_series: dict[str, pd.Series] = {}
    priced: set[str] = set()
    missing: list[str] = []
    rejection = Counter()

    batches = list(chunks(scan_universe, BATCH_SIZE))
    for bn, batch in enumerate(batches, 1):
        tickers = [s.ticker for s in batch]
        try:
            raw = download_batch(tickers)
        except Exception as exc:
            print(f"[{category}] batch {bn}/{len(batches)} failed: {type(exc).__name__}: {exc}")
            missing.extend(tickers)
            time.sleep(1.0)
            continue
        for s in batch:
            rf = frame_for(raw, s.ticker)
            if rf.empty:
                missing.append(s.ticker)
                continue
            priced.add(s.ticker)
            f = normalize_market_frame(rf, category)
            if len(f) < MIN_TRADING_DAYS:
                rejection["U1_listed_lt_280d"] += 1
                continue
            ind = add_indicators(f)
            u = u_eligible_series(ind, category, liquidity_native)
            frames[s.ticker] = f
            indicators[s.ticker] = ind
            eligible_series[s.ticker] = u
        if bn % 10 == 0 or bn == len(batches):
            print(f"[{category}] {bn}/{len(batches)} batches | priced={len(priced):,} | normalized={len(frames):,}")
        time.sleep(random.uniform(*PRIMARY_BATCH_SLEEP))

    retry = [t for t in dict.fromkeys(missing) if t not in priced and t in by_ticker]
    if retry:
        remaining = retry
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            next_remaining = []
            for batch in chunks(remaining, RETRY_BATCH_SIZE):
                try:
                    raw = download_batch(batch, timeout=55)
                except Exception:
                    next_remaining.extend(batch)
                    continue
                for tkr in batch:
                    rf = frame_for(raw, tkr)
                    if rf.empty:
                        next_remaining.append(tkr)
                        continue
                    priced.add(tkr)
                    f = normalize_market_frame(rf, category)
                    if len(f) < MIN_TRADING_DAYS:
                        rejection["U1_listed_lt_280d"] += 1
                        continue
                    ind = add_indicators(f)
                    frames[tkr] = f
                    indicators[tkr] = ind
                    eligible_series[tkr] = u_eligible_series(ind, category, liquidity_native)
                time.sleep(random.uniform(*RETRY_BATCH_SLEEP))
            if set(next_remaining) == set(remaining):
                break
            remaining = list(dict.fromkeys(next_remaining))
            if not remaining:
                break

    coverage = len(priced) / max(1, len(scan_universe))
    if coverage < MIN_COVERAGE[category]:
        raise RuntimeError(f"{category} price coverage too low: {len(priced)}/{len(scan_universe)} ({coverage:.1%})")

    # Current U filter count and reasons.
    current_u: dict[str, bool] = {}
    for tkr, ind in indicators.items():
        u = eligible_series[tkr]
        ok = bool(len(u) and u.iloc[-1])
        current_u[tkr] = ok
        if not ok:
            if finite(ind["AvgTurnover20"].iloc[-1]) < liquidity_native:
                rejection["U2_turnover_lt_1b_krw"] += 1
            elif finite(ind["RawVolume"].iloc[-1], 0.0) <= 0:
                rejection["U6_halted_or_zero_volume"] += 1
            elif category in KR_CATEGORIES and finite(ind["RawClose"].pct_change().iloc[-1]) >= KR_LIMIT_UP_GUARD:
                rejection["U5_limit_up_close"] += 1
            else:
                rejection["U_filter_other"] += 1

    # Current diagnostic RS_swing is cross-sectional and does not affect scoring.
    rs_swing_pct = current_rs_swing_percentiles(indicators, current_u)

    # Relative strength uses the entire U-filter-passed cross section point-in-time.
    rs126_matrix = build_cross_sectional_rs(indicators, eligible_series, 126)
    rs63_matrix = build_cross_sectional_rs(indicators, eligible_series, 63)

    results: list[dict] = []
    global_pb_pool: list[float] = []
    global_bo_pool: list[float] = []

    for idx, (tkr, ind) in enumerate(indicators.items(), 1):
        if not current_u.get(tkr):
            continue
        stock = by_ticker[tkr]
        t = len(ind) - 1
        date = ind.index[t]
        M = _aligned_value(regime, date)
        if not np.isfinite(M):
            rejection["market_regime_unavailable"] += 1
            continue

        rs126 = rs126_matrix[tkr].reindex(ind.index) if tkr in rs126_matrix.columns else pd.Series(np.nan, index=ind.index)
        rs63 = rs63_matrix[tkr].reindex(ind.index) if tkr in rs63_matrix.columns else pd.Series(np.nan, index=ind.index)
        pb = eval_pullback(ind, t, finite(rs126.iloc[t]), M)
        bo = eval_breakout(ind, t, finite(rs63.iloc[t]), M)
        pb, bo = adopt_exclusive(pb, bo)

        if scan_mode == "FULL":
            pb_bt, bo_bt, pb_pool, bo_pool = backtest_both(ind, category, eligible_series[tkr], rs126, rs63, regime, benchmark)
            global_pb_pool.extend(pb_pool)
            global_bo_pool.extend(bo_pool)
        else:
            pb_bt, bo_bt = {}, {}

        if not pb.get("adopted") and not bo.get("adopted"):
            if pb.get("gate_pass") and not pb.get("eligible"):
                rejection["PB_score_lt_50"] += 1
            elif not pb.get("gate_pass"):
                rejection["PB_gate_fail"] += 1
            if bo.get("gate_pass") and not bo.get("eligible"):
                rejection["BO_score_lt_50"] += 1
            elif not bo.get("gate_pass"):
                rejection["BO_gate_fail"] += 1
            continue

        close = finite(ind["Close"].iloc[-1])
        market_size_krw, market_size_basis = resolve_market_size_display(stock, close, usdkrw, size_cache)
        prev_close = finite(ind["Close"].iloc[-2])
        day_change = close / prev_close - 1.0 if prev_close > 0 else np.nan

        pb_metrics = dict(pb.get("metrics") or {})
        pb_metrics["RS_swing"] = clean(rs_swing_pct.get(tkr), 2)
        bo_metrics = dict(bo.get("metrics") or {})
        # V3 requests BBWidth_percentile only as a diagnostic comparison, but the
        # supplied V3 document intentionally deletes the Bollinger stream and does
        # not define its diagnostic parameters. Do not invent an unstated formula.
        bo_metrics.setdefault("BBWidth_percentile", None)
        pb_metrics.update({"market_size_krw": clean(market_size_krw, 0), "market_size_basis": market_size_basis})
        bo_metrics.update({"market_size_krw": clean(market_size_krw, 0), "market_size_basis": market_size_basis})

        item = {
            "ticker": stock.ticker, "symbol": stock.symbol, "name": stock.name, "category": stock.category,
            "exchange": stock.exchange, "currency": stock.currency, "date": pd.Timestamp(date).date().isoformat(),
            "close": clean(close), "day_change_pct": clean(day_change * 100.0, 2),
            # PB kept top-level for UI backward compatibility.
            "eligible": bool(pb.get("adopted")), "rank": None, "score": clean(pb.get("score"), 3) or 0.0,
            "raw_score": clean(pb.get("raw_score"), 3), "M": clean(pb.get("M"), 2),
            "scores": pb.get("scores") or {}, "weights": pb.get("weights") or {}, "contributions": pb.get("contributions") or {},
            "gates": pb.get("gates") or {}, "metrics": pb_metrics, "backtest": pb_bt,
            "rising": {
                "eligible": bool(bo.get("adopted")), "rank": None, "score": clean(bo.get("score"), 3) or 0.0,
                "raw_score": clean(bo.get("raw_score"), 3), "M": clean(bo.get("M"), 2),
                "scores": bo.get("scores") or {}, "weights": bo.get("weights") or {}, "contributions": bo.get("contributions") or {},
                "gates": bo.get("gates") or {}, "metrics": bo_metrics, "backtest": bo_bt,
            },
            "chart": _make_chart(ind),
        }
        results.append(item)
        if idx % 250 == 0:
            print(f"[{category}] strategy evaluation {idx}/{len(indicators)} | candidates={len(results)}")

    # StrategyPercentile: current Score vs pooled gate-pass Score distribution over recent 250 sessions.
    for item in results:
        if item.get("eligible"):
            item["metrics"]["StrategyPercentile"] = clean(percentile_rank(item["score"], global_pb_pool) * 100.0, 2) if global_pb_pool else None
        if (item.get("rising") or {}).get("eligible"):
            item["rising"]["metrics"]["StrategyPercentile"] = clean(percentile_rank(item["rising"]["score"], global_bo_pool) * 100.0, 2) if global_bo_pool else None

    pb_items = sorted([x for x in results if x.get("eligible")], key=lambda x: (-finite(x.get("score"), 0), x["symbol"]))
    bo_items = sorted([x for x in results if (x.get("rising") or {}).get("eligible")], key=lambda x: (-finite(x["rising"].get("score"), 0), x["symbol"]))
    for r, x in enumerate(pb_items, 1):
        x["rank"] = r
    for r, x in enumerate(bo_items, 1):
        x["rising"]["rank"] = r

    # Unique detail rows across two strategy lists.
    items = sorted(results, key=lambda x: min(x.get("rank") or 10**9, (x.get("rising") or {}).get("rank") or 10**9))
    market_date = max((x["date"] for x in items), default=None)
    current_M = _aligned_value(regime, pd.Timestamp(market_date)) if market_date else np.nan
    max_entries = round(5 * current_M) if np.isfinite(current_M) else None

    payload_meta = {
        "app": "Morning Invest",
        "strategy": "MI_V10_V3_PULLBACK_BREAKOUT",
        "spec": "눌림목_돌파_스캐닝_로직_V3",
        "modes": ["cheap", "rising"],
        "mode_labels": {"cheap": "싼게 좋아 · 눌림목", "rising": "오르는게 좋아 · 돌파"},
        "category": category, "category_label": CATEGORY_LABEL[category],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scan_mode": scan_mode, "data_status": "close_confirmed", "market_date": market_date,
        "universe_source": universe_source, "restriction_snapshot": restriction_meta,
        "universe_count": len(universe), "price_download_universe_count": len(scan_universe),
        "priced_count": len(priced), "coverage_pct": round(coverage * 100, 1),
        "u_filter_passed_count": sum(current_u.values()), "candidate_detail_count": len(items),
        "cheap_count": len(pb_items), "rising_count": len(bo_items),
        "market_regime_M": clean(current_M, 2), "max_entries": max_entries,
        "thresholds": {"avg_turnover_20d_krw": MIN_AVG_TURNOVER_KRW_20D, "usdkrw": clean(usdkrw, 4)},
        "filter_counts": dict(sorted(rejection.items())),
        "score_scale": "0_to_100_final_after_market_regime",
        "entry_threshold": ENTRY_SCORE_THRESHOLD,
        "etf_whitelist": {
            "enabled": category in ETF_CATEGORIES,
            "expected_count": 300 if category == "KR_ETF" else (500 if category == "US_ETF" else None),
            "source": "attached_user_xlsx" if category in ETF_CATEGORIES else None,
        },
        "benchmark": BENCHMARK_TICKER[category],
        "backtest_model": {
            "history": "last_252_evaluable_signal_sessions",
            "entry": "t_plus_1_open", "cooldown_sessions": BACKTEST_COOLDOWN_DAYS,
            "forward_sessions": [5, 10, 20], "validation_return": "benchmark_excess_return",
            "execution_filters": ["next_day_halt", "gap_ge_29pct", "entry_le_stop"],
            "limitations": ["historical_regulatory_status_not_reconstructed", "survivorship_bias_current_listing_universe"],
        },
    }

    out_dir, detail_count = _write_category_site(category, payload_meta, items, size_cache, scan_mode)
    print(
        f"[{category}] DONE | U-pass={sum(current_u.values()):,} | PB={len(pb_items):,} | BO={len(bo_items):,} | "
        f"details={detail_count:,} | coverage={coverage:.1%} | M={current_M}"
    )
    print(f"[{category}] {out_dir / 'summary.json'} {(out_dir / 'summary.json').stat().st_size / 1024:.1f} KB")


def main():
    parser = argparse.ArgumentParser(description="Morning Invest v10 V3 technical screener")
    parser.add_argument("--market", default="ALL", choices=["ALL", "KR", "KR_ETF", "KR_GROUP", "US", "US_ETF", "US_GROUP"])
    parser.add_argument("--scan-mode", default="FULL", choices=["FULL", "QUICK"], help="V3 always uses confirmed bars; QUICK only skips backtest recomputation.")
    args = parser.parse_args()

    groups = {
        "ALL": ["KR", "KR_ETF", "US", "US_ETF"],
        "KR_GROUP": ["KR", "KR_ETF"], "US_GROUP": ["US", "US_ETF"],
        "KR": ["KR"], "KR_ETF": ["KR_ETF"], "US": ["US"], "US_ETF": ["US_ETF"],
    }
    categories = groups[args.market]
    usdkrw = fetch_usdkrw() if any(c in {"US", "US_ETF"} for c in categories) else None

    failed = []
    for category in categories:
        try:
            scan_category(category, usdkrw, args.scan_mode)
        except Exception as exc:
            failed.append((category, str(exc)))
            print(f"ERROR {category}: {type(exc).__name__}: {exc}")
            traceback.print_exc()

    if failed:
        raise SystemExit(" | ".join(f"{c}: {m}" for c, m in failed))


if __name__ == "__main__":
    main()
