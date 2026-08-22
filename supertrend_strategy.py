from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

SUPER_TREND_PERIOD = 10
SUPER_TREND_MULTIPLIER = 2.0
BACKTEST_SESSIONS = 504  # fallback when the index is not datetime-like
CHART_SESSIONS = 126     # ~6 trading months

OPINION_ORDER = {
    "BUY_S": 0,
    "BUY_A": 1,
    "BUY_B": 2,
    "BUY_C": 3,
    "HOLD": 4,
    "SELL": 5,
}

OPINION_LABEL = {
    "BUY_S": "Buy S",
    "BUY_A": "Buy A",
    "BUY_B": "Buy B",
    "BUY_C": "Buy C",
    "HOLD": "Hold",
    "SELL": "매도",
}


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype(float)


def _ohlc(df: pd.DataFrame) -> pd.DataFrame:
    required = ["Open", "High", "Low", "Close"]
    if any(c not in df.columns for c in required):
        return pd.DataFrame(columns=required)
    out = pd.DataFrame(index=df.index)
    for c in required:
        out[c] = _num(df[c])
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=required)
    out = out[(out["High"] >= out["Low"]) & (out["Close"] > 0)]
    return out


def supertrend(df: pd.DataFrame, period: int = SUPER_TREND_PERIOD, multiplier: float = SUPER_TREND_MULTIPLIER) -> pd.DataFrame:
    """Return causal Supertrend series using Wilder ATR (RMA), period=10/multiplier=2 by default."""
    ohlc = _ohlc(df)
    if ohlc.empty:
        return pd.DataFrame(index=df.index, columns=["atr", "supertrend", "direction"], dtype=float)

    high = ohlc["High"]
    low = ohlc["Low"]
    close = ohlc["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Wilder RMA. min_periods keeps the first period-1 values invalid rather than
    # manufacturing early signals.
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    n = len(ohlc)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.full(n, np.nan)

    h = high.to_numpy(float)
    l = low.to_numpy(float)
    c = close.to_numpy(float)
    a = atr.to_numpy(float)
    bu = basic_upper.to_numpy(float)
    bl = basic_lower.to_numpy(float)
    hl = hl2.to_numpy(float)

    first = next((i for i, v in enumerate(a) if np.isfinite(v)), None)
    if first is None:
        result = pd.DataFrame(index=ohlc.index, data={"atr": atr, "supertrend": st, "direction": direction})
        return result.reindex(df.index)

    final_upper[first] = bu[first]
    final_lower[first] = bl[first]
    direction[first] = 1.0 if c[first] >= hl[first] else -1.0
    st[first] = final_lower[first] if direction[first] > 0 else final_upper[first]

    for i in range(first + 1, n):
        if not all(np.isfinite(v) for v in (bu[i], bl[i], c[i], c[i - 1])):
            continue

        prev_fu = final_upper[i - 1]
        prev_fl = final_lower[i - 1]
        final_upper[i] = bu[i] if (not np.isfinite(prev_fu) or bu[i] < prev_fu or c[i - 1] > prev_fu) else prev_fu
        final_lower[i] = bl[i] if (not np.isfinite(prev_fl) or bl[i] > prev_fl or c[i - 1] < prev_fl) else prev_fl

        prev_st = st[i - 1]
        if not np.isfinite(prev_st):
            direction[i] = 1.0 if c[i] >= hl[i] else -1.0
        elif np.isfinite(prev_fu) and abs(prev_st - prev_fu) <= max(1e-12, abs(prev_fu) * 1e-12):
            direction[i] = 1.0 if c[i] > final_upper[i] else -1.0
        else:
            direction[i] = -1.0 if c[i] < final_lower[i] else 1.0

        st[i] = final_lower[i] if direction[i] > 0 else final_upper[i]

    result = pd.DataFrame(index=ohlc.index, data={
        "atr": atr.to_numpy(float),
        "supertrend": st,
        "direction": direction,
    })
    return result.reindex(df.index)


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Heikin-Ashi candles for display only; signals remain based solely on standard-OHLC Supertrend."""
    ohlc = _ohlc(df)
    if ohlc.empty:
        return pd.DataFrame(index=df.index, columns=["ha_open", "ha_high", "ha_low", "ha_close"], dtype=float)

    o = ohlc["Open"].to_numpy(float)
    h = ohlc["High"].to_numpy(float)
    l = ohlc["Low"].to_numpy(float)
    c = ohlc["Close"].to_numpy(float)
    ha_c = (o + h + l + c) / 4.0
    ha_o = np.empty(len(ohlc), dtype=float)
    ha_o[0] = (o[0] + c[0]) / 2.0
    for i in range(1, len(ohlc)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
    ha_h = np.maximum.reduce([h, ha_o, ha_c])
    ha_l = np.minimum.reduce([l, ha_o, ha_c])
    out = pd.DataFrame(index=ohlc.index, data={
        "ha_open": ha_o,
        "ha_high": ha_h,
        "ha_low": ha_l,
        "ha_close": ha_c,
    })
    return out.reindex(df.index)


def _opinion_for(close: float, p0: float, p1: float, direction: float) -> tuple[str, float | None]:
    if not np.isfinite(direction):
        return "HOLD", None
    if direction < 0:
        return "SELL", None
    if not (np.isfinite(p0) and p0 > 0 and np.isfinite(p1)):
        return "HOLD", None
    if p1 < p0:
        return "HOLD", (close / p0 - 1.0) * 100.0 if np.isfinite(close) else None

    distance = (close / p0 - 1.0) * 100.0 if np.isfinite(close) and close > 0 else np.nan
    if not np.isfinite(distance) or distance < 0:
        return "HOLD", float(distance) if np.isfinite(distance) else None
    if distance < 2.0:
        return "BUY_S", float(distance)
    if distance < 5.0:
        return "BUY_A", float(distance)
    if distance < 10.0:
        return "BUY_B", float(distance)
    if distance < 20.0:
        return "BUY_C", float(distance)
    return "HOLD", float(distance)


def signal_series(df: pd.DataFrame, period: int = SUPER_TREND_PERIOD, multiplier: float = SUPER_TREND_MULTIPLIER) -> pd.DataFrame:
    ohlc = _ohlc(df)
    st = supertrend(ohlc, period=period, multiplier=multiplier)
    out = ohlc.join(st, how="left")
    p0 = np.nan
    p0_values = []
    opinions = []
    distances = []
    prev_direction = np.nan
    for _, row in out.iterrows():
        d = float(row["direction"]) if np.isfinite(row["direction"]) else np.nan
        stv = float(row["supertrend"]) if np.isfinite(row["supertrend"]) else np.nan
        close = float(row["Close"]) if np.isfinite(row["Close"]) else np.nan
        if np.isfinite(d) and d > 0 and np.isfinite(prev_direction) and prev_direction < 0 and np.isfinite(stv):
            p0 = stv
        op, dist = _opinion_for(close, p0, stv, d)
        p0_values.append(p0)
        opinions.append(op)
        distances.append(dist)
        if np.isfinite(d):
            prev_direction = d
    out["p0"] = p0_values
    out["opinion"] = opinions
    out["distance_pct"] = distances
    return out


def backtest_buy_s_to_sell(df: pd.DataFrame, sessions: int = BACKTEST_SESSIONS, signals: pd.DataFrame | None = None) -> dict[str, Any]:
    signals = signal_series(df) if signals is None else signals
    valid = signals.dropna(subset=["Close", "supertrend", "direction"])
    if valid.empty:
        return {"available": False, "reason": "insufficient_history", "trades": 0, "avg_return_pct": None}

    try:
        last_ts = pd.Timestamp(valid.index[-1])
        cutoff = last_ts - pd.DateOffset(years=2)
        window = valid.loc[valid.index >= cutoff]
    except Exception:
        start_pos = max(0, len(valid) - sessions)
        window = valid.iloc[start_pos:]
    holding = False
    entry_price = np.nan
    entry_date = None
    trades: list[dict[str, Any]] = []

    for idx, row in window.iterrows():
        op = str(row.get("opinion") or "HOLD")
        close = float(row["Close"])
        if not holding and op == "BUY_S":
            holding = True
            entry_price = close
            entry_date = pd.Timestamp(idx).date().isoformat()
        elif holding and op == "SELL":
            ret = (close / entry_price - 1.0) * 100.0
            trades.append({
                "buy_date": entry_date,
                "buy_price": float(entry_price),
                "sell_date": pd.Timestamp(idx).date().isoformat(),
                "sell_price": float(close),
                "return_pct": float(ret),
            })
            holding = False
            entry_price = np.nan
            entry_date = None

    if not trades:
        return {
            "available": True,
            "trades": 0,
            "avg_return_pct": None,
            "win_rate_pct": None,
            "open_position": bool(holding),
            "period_sessions": int(len(window)),
        "period_start": pd.Timestamp(window.index[0]).date().isoformat() if len(window) else None,
            "recent_trades": [],
        }

    returns = np.array([t["return_pct"] for t in trades], dtype=float)
    return {
        "available": True,
        "trades": int(len(trades)),
        "avg_return_pct": float(np.mean(returns)),
        "win_rate_pct": float(np.mean(returns > 0) * 100.0),
        "open_position": bool(holding),
        "period_sessions": int(len(window)),
        "period_start": pd.Timestamp(window.index[0]).date().isoformat() if len(window) else None,
        "recent_trades": trades[-10:],
    }


def analyze(df: pd.DataFrame, period: int = SUPER_TREND_PERIOD, multiplier: float = SUPER_TREND_MULTIPLIER) -> dict[str, Any]:
    signals = signal_series(df, period=period, multiplier=multiplier)
    valid = signals.dropna(subset=["Close"])
    if valid.empty:
        return {"available": False, "opinion": "HOLD", "opinion_label": "Hold", "rank_level": OPINION_ORDER["HOLD"]}

    row = valid.iloc[-1]
    direction = float(row["direction"]) if np.isfinite(row["direction"]) else np.nan
    p1 = float(row["supertrend"]) if np.isfinite(row["supertrend"]) else np.nan
    p0 = float(row["p0"]) if np.isfinite(row["p0"]) else np.nan
    close = float(row["Close"])
    opinion = str(row.get("opinion") or "HOLD")
    distance = float(row["distance_pct"]) if np.isfinite(row["distance_pct"]) else None

    bt = backtest_buy_s_to_sell(df, signals=signals)
    ha = heikin_ashi(df).reindex(signals.index)
    chart_idx = signals.index[-CHART_SESSIONS:]
    chart = []
    for idx in chart_idx:
        sr = signals.loc[idx]
        hr = ha.loc[idx] if idx in ha.index else None
        if hr is None or not all(np.isfinite(hr[k]) for k in ["ha_open", "ha_high", "ha_low", "ha_close"]):
            continue
        stv = float(sr["supertrend"]) if np.isfinite(sr["supertrend"]) else None
        d = int(sr["direction"]) if np.isfinite(sr["direction"]) else None
        chart.append({
            "date": pd.Timestamp(idx).date().isoformat(),
            "open": float(hr["ha_open"]),
            "high": float(hr["ha_high"]),
            "low": float(hr["ha_low"]),
            "close": float(hr["ha_close"]),
            "supertrend": stv,
            "direction": d,
        })

    return {
        "available": bool(np.isfinite(p1) and np.isfinite(direction)),
        "period": int(period),
        "multiplier": float(multiplier),
        "direction": "UP" if np.isfinite(direction) and direction > 0 else "DOWN" if np.isfinite(direction) else None,
        "p0": float(p0) if np.isfinite(p0) else None,
        "p1": float(p1) if np.isfinite(p1) else None,
        "current_close": close,
        "distance_from_p0_pct": distance,
        "opinion": opinion,
        "opinion_label": OPINION_LABEL.get(opinion, opinion),
        "rank_level": int(OPINION_ORDER.get(opinion, OPINION_ORDER["HOLD"])),
        "backtest": bt,
        "chart": chart,
    }
