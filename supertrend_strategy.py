from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# DTC Local v1.14.2 PrevDownSTGate + BUY->SELL Cycle specification.
SUPER_TREND_PERIOD = 14
SUPER_TREND_MULTIPLIER = 2.0
ADX_DI_LENGTH = 14
ADX_SMOOTHING = 14
BACKTEST_YEARS = 2
CHART_SESSIONS = 126
# The web scanner still keeps its inherited >=604-session hard filter.  The
# algorithm itself only needs enough history for ST/ADX warm-up, but keeping the
# scanner filter makes the 2Y cycle test stable and deterministic.
MIN_REQUIRED_BARS = 604

OPINION_ORDER = {
    "STRONG_BUY": 0,
    "BUY": 1,
    "HOLD": 2,
    "SELL": 3,
    "STRONG_SELL": 4,
}

OPINION_LABEL = {
    "STRONG_BUY": "STRONG BUY",
    "BUY": "BUY",
    "HOLD": "HOLD",
    "SELL": "SELL",
    "STRONG_SELL": "STRONG SELL",
}


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype(float)


def _ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Valid adjusted OHLC only. Missing sessions are omitted, never forward-filled."""
    required = ["Open", "High", "Low", "Close"]
    if df is None or df.empty or any(c not in df.columns for c in required):
        return pd.DataFrame(columns=required)
    out = pd.DataFrame(index=pd.DatetimeIndex(df.index))
    for c in required:
        out[c] = _num(df[c]).to_numpy()
    out["Volume"] = _num(df["Volume"]).to_numpy() if "Volume" in df.columns else 0.0
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=required)
    out = out[
        (out["High"] >= out["Low"])
        & (out["Open"] > 0)
        & (out["High"] > 0)
        & (out["Low"] > 0)
        & (out["Close"] > 0)
    ]
    out["Volume"] = out["Volume"].fillna(0.0).clip(lower=0.0)
    return out[~out.index.duplicated(keep="last")].sort_index()


def _rma(values: np.ndarray, length: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), np.nan, dtype=float)
    if length <= 0 or len(values) < length:
        return out
    seed = values[:length]
    if not np.all(np.isfinite(seed)):
        return out
    out[length - 1] = float(np.mean(seed))
    for i in range(length, len(values)):
        if np.isfinite(values[i]) and np.isfinite(out[i - 1]):
            out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return out


def _rma_from_first_finite(values: np.ndarray, length: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), np.nan, dtype=float)
    finite = np.flatnonzero(np.isfinite(values))
    if length <= 0 or len(finite) < length:
        return out
    first = int(finite[0])
    seed_end = first + length
    if seed_end > len(values):
        return out
    seed_values = values[first:seed_end]
    if np.count_nonzero(np.isfinite(seed_values)) < length:
        return out
    seed_idx = seed_end - 1
    out[seed_idx] = float(np.mean(seed_values))
    for i in range(seed_idx + 1, len(values)):
        if np.isfinite(values[i]) and np.isfinite(out[i - 1]):
            out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return out


def supertrend(
    df: pd.DataFrame,
    length: int = SUPER_TREND_PERIOD,
    multiplier: float = SUPER_TREND_MULTIPLIER,
) -> pd.DataFrame:
    """SuperTrend used by DTC Local v1.14: ST(14,2), Wilder ATR, close-based flips."""
    d = _ohlc(df).copy()
    if d.empty:
        return d.assign(ATR=np.nan, ST_UPPER=np.nan, ST_LOWER=np.nan, ST=np.nan, ST_DIR=0)

    high = d["High"].to_numpy(float)
    low = d["Low"].to_numpy(float)
    close = d["Close"].to_numpy(float)
    n = len(d)

    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    atr = _rma(tr, int(length))
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + float(multiplier) * atr
    basic_lower = hl2 - float(multiplier) * atr

    upper = np.full(n, np.nan, dtype=float)
    lower = np.full(n, np.nan, dtype=float)
    st = np.full(n, np.nan, dtype=float)
    direction = np.zeros(n, dtype=int)
    first = int(length) - 1
    if n <= first:
        d["ATR"] = atr
        d["ST_UPPER"] = upper
        d["ST_LOWER"] = lower
        d["ST"] = st
        d["ST_DIR"] = direction
        return d

    upper[first] = basic_upper[first]
    lower[first] = basic_lower[first]
    direction[first] = -1
    st[first] = upper[first]

    for i in range(first + 1, n):
        upper[i] = basic_upper[i] if (basic_upper[i] < upper[i - 1] or close[i - 1] > upper[i - 1]) else upper[i - 1]
        lower[i] = basic_lower[i] if (basic_lower[i] > lower[i - 1] or close[i - 1] < lower[i - 1]) else lower[i - 1]
        if math.isclose(st[i - 1], upper[i - 1], rel_tol=1e-12, abs_tol=1e-12):
            if close[i] > upper[i]:
                direction[i] = 1
                st[i] = lower[i]
            else:
                direction[i] = -1
                st[i] = upper[i]
        else:
            if close[i] < lower[i]:
                direction[i] = -1
                st[i] = upper[i]
            else:
                direction[i] = 1
                st[i] = lower[i]

    d["ATR"] = atr
    d["ST_UPPER"] = upper
    d["ST_LOWER"] = lower
    d["ST"] = st
    d["ST_DIR"] = direction
    return d


def adx(
    df: pd.DataFrame,
    di_length: int = ADX_DI_LENGTH,
    adx_smoothing: int = ADX_SMOOTHING,
) -> pd.DataFrame:
    d = _ohlc(df).copy()
    n = len(d)
    if n == 0:
        for col in ("PLUS_DI", "MINUS_DI", "DX", "ADX"):
            d[col] = pd.Series(dtype=float)
        return d

    high = d["High"].to_numpy(float)
    low = d["Low"].to_numpy(float)
    close = d["Close"].to_numpy(float)
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])

    up_move = np.diff(high, prepend=high[0])
    down_move = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr = _rma(tr, int(di_length))
    plus_sm = _rma(plus_dm, int(di_length))
    minus_sm = _rma(minus_dm, int(di_length))
    plus_di = np.full(n, np.nan, dtype=float)
    minus_di = np.full(n, np.nan, dtype=float)
    valid_atr = np.isfinite(atr) & (atr > 0)
    plus_di[valid_atr] = 100.0 * plus_sm[valid_atr] / atr[valid_atr]
    minus_di[valid_atr] = 100.0 * minus_sm[valid_atr] / atr[valid_atr]

    denom = plus_di + minus_di
    dx = np.full(n, np.nan, dtype=float)
    valid = np.isfinite(denom) & (denom > 0)
    dx[valid] = 100.0 * np.abs(plus_di[valid] - minus_di[valid]) / denom[valid]
    zero = np.isfinite(plus_di) & np.isfinite(minus_di) & (denom == 0)
    dx[zero] = 0.0

    d["PLUS_DI"] = plus_di
    d["MINUS_DI"] = minus_di
    d["DX"] = dx
    d["ADX"] = _rma_from_first_finite(dx, int(adx_smoothing))
    return d


def add_up_flip_reference(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the previous DOWN SuperTrend price at the latest DOWN->UP flip.

    DTC Local v1.14.2 rule:
    * P0 is ST[i-1], the last DOWN SuperTrend value immediately before flip.
    * The UP-flip bar itself has age 0 and cannot be STRONG BUY.
    * From the next UP bar onward, current UP ST must reach/exceed P0.
    """
    d = df.copy()
    n = len(d)
    refs = np.full(n, np.nan, dtype=float)
    ages = np.full(n, np.nan, dtype=float)
    current_ref = np.nan
    current_age = -1
    dirs = pd.to_numeric(d.get("ST_DIR", pd.Series(index=d.index, dtype=float)), errors="coerce").fillna(0).astype(int).to_numpy()
    sts = pd.to_numeric(d.get("ST", pd.Series(index=d.index, dtype=float)), errors="coerce").to_numpy(dtype=float)
    for i in range(n):
        is_flip = (
            i > 0
            and dirs[i] == 1
            and dirs[i - 1] == -1
            and np.isfinite(sts[i - 1])
        )
        if is_flip:
            current_ref = float(sts[i - 1])
            current_age = 0
        if dirs[i] == 1 and np.isfinite(current_ref):
            refs[i] = current_ref
            ages[i] = float(current_age)
            current_age += 1
        elif dirs[i] != 1:
            current_age = -1
    d["ST_UP_FLIP_REF"] = refs
    d["ST_UP_FLIP_AGE"] = ages
    return d


def classify_supertrad_index(
    st_direction: int,
    adx_value: float,
    st_value: float | None = None,
    up_flip_st: float | None = None,
    up_flip_age: int | float | None = None,
) -> tuple[str, str]:
    """Exact DTC Local v1.14.2 Supertrad Index decision table."""
    if adx_value is None or not np.isfinite(float(adx_value)):
        return "HOLD", "ADX(14,14) 계산값 부족"
    a = float(adx_value)
    if a >= 70.0:
        return "STRONG_SELL", f"ADX(14,14) {a:.1f} ≥ 70 · SuperTrend와 무관"
    if a >= 40.0:
        return "SELL", f"ADX(14,14) {a:.1f} ≥ 40 · SuperTrend와 무관"
    if int(st_direction) == 1 and 20.0 <= a < 25.0:
        age_ok = (
            up_flip_age is None
            or (np.isfinite(float(up_flip_age)) and float(up_flip_age) >= 1.0)
        )
        gate_ok = (
            st_value is not None
            and up_flip_st is not None
            and np.isfinite(float(st_value))
            and np.isfinite(float(up_flip_st))
            and age_ok
            and float(st_value) >= float(up_flip_st)
        )
        if gate_ok:
            return "STRONG_BUY", (
                f"SuperTrend(14,2) 상승 · 현재 ST {float(st_value):,.2f} ≥ 직전 하락 ST {float(up_flip_st):,.2f} · "
                f"20 ≤ ADX(14,14) {a:.1f} < 25"
            )
        return "HOLD", (
            f"SuperTrend 상승이나 ST 돌파조건 미충족 · 현재 ST {float(st_value):,.2f} / 직전 하락 ST {float(up_flip_st):,.2f}"
            if st_value is not None and up_flip_st is not None and np.isfinite(float(st_value)) and np.isfinite(float(up_flip_st))
            else "SuperTrend 상승이나 직전 하락 ST 기준가격이 아직 없습니다."
        )
    if int(st_direction) == 1 and 25.0 <= a < 30.0:
        return "BUY", f"SuperTrend(14,2) 상승 · 25 ≤ ADX(14,14) {a:.1f} < 30"
    if a < 20.0:
        return "HOLD", f"ADX(14,14) {a:.1f} < 20"
    if 30.0 <= a < 40.0:
        return "HOLD", f"30 ≤ ADX(14,14) {a:.1f} < 40"
    return "HOLD", f"SuperTrend(14,2) 하락 · ADX(14,14) {a:.1f} < 40"


def signal_series(
    df: pd.DataFrame,
    period: int = SUPER_TREND_PERIOD,
    multiplier: float = SUPER_TREND_MULTIPLIER,
    di_length: int = ADX_DI_LENGTH,
    adx_smoothing: int = ADX_SMOOTHING,
) -> pd.DataFrame:
    st = supertrend(df, period, multiplier)
    if st.empty:
        return st
    d = adx(st[["Open", "High", "Low", "Close", "Volume"]], di_length, adx_smoothing)
    # Preserve the ST columns computed above.
    for col in ("ATR", "ST_UPPER", "ST_LOWER", "ST", "ST_DIR"):
        d[col] = st[col]
    d = add_up_flip_reference(d)

    ops: list[str] = []
    reasons: list[str] = []
    dirs = d["ST_DIR"].fillna(0).astype(int).to_numpy()
    adxs = d["ADX"].to_numpy(float)
    sts = d["ST"].to_numpy(float)
    refs = d["ST_UP_FLIP_REF"].to_numpy(float)
    ages = d["ST_UP_FLIP_AGE"].to_numpy(float)
    for i in range(len(d)):
        op, reason = classify_supertrad_index(dirs[i], adxs[i], sts[i], refs[i], ages[i])
        ops.append(op)
        reasons.append(reason)
    d["opinion_code"] = ops
    d["opinion"] = [OPINION_LABEL[x] for x in ops]
    d["reason"] = reasons
    return d


def _date_text(ts: Any) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def compute_buy_cycles(enriched: pd.DataFrame, years: int = BACKTEST_YEARS) -> dict[str, Any]:
    """Exact DTC Local v1.14 BUY->SELL cycle backtest.

    * first STRONG BUY/BUY while flat enters at the signal-day Close
    * ignore later buy signals while active
    * first SELL/STRONG SELL exits at the signal-day Close
    * next entry requires >=1 ST-down bar since the prior entry
    * max return is peak High from entry through exit / entry Close
    * headline BACKTEST is median max return of completed cycles only
    """
    if enriched is None or enriched.empty:
        return {"events": [], "median_max_return_pct": None, "completed_events": 0}
    d = add_up_flip_reference(enriched.copy()) if "ST_UP_FLIP_REF" not in enriched.columns else enriched.copy()
    idx = pd.to_datetime(d.index)
    cutoff = idx.max() - pd.DateOffset(years=years)

    events: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    has_ever_entered = False
    down_seen_since_entry = False
    ready_after_exit = True

    for i, (_, row) in enumerate(d.iterrows()):
        st_dir = int(row.get("ST_DIR", 0) or 0)
        av = row.get("ADX", np.nan)
        stv = row.get("ST", np.nan)
        flip_ref = row.get("ST_UP_FLIP_REF", np.nan)
        flip_age = row.get("ST_UP_FLIP_AGE", np.nan)

        if st_dir == -1 and active is not None:
            down_seen_since_entry = True
        if st_dir == -1 and active is None and has_ever_entered:
            ready_after_exit = True
        if st_dir == 0 or pd.isna(av):
            continue
        op, reason = classify_supertrad_index(st_dir, float(av), stv, flip_ref, flip_age)

        if active is not None:
            hi = row.get("High", np.nan)
            if pd.notna(hi) and np.isfinite(float(hi)):
                hi = float(hi)
                if active.get("_peak_price") is None or hi > float(active["_peak_price"]):
                    active["_peak_price"] = hi
                    active["_peak_time"] = _date_text(d.index[i])
            if op in ("SELL", "STRONG_SELL"):
                active["completed"] = True
                active["exit_time"] = _date_text(d.index[i])
                active["exit_price"] = float(row["Close"]) if pd.notna(row.get("Close")) else None
                active["exit_opinion"] = op
                active["exit_adx"] = float(av)
                active["sell_reason"] = reason
                active["peak_time"] = active.pop("_peak_time", "")
                active["peak_price"] = active.pop("_peak_price", None)
                if active.get("peak_price") is not None and active.get("entry_price"):
                    active["max_return_pct"] = (float(active["peak_price"]) / float(active["entry_price"]) - 1.0) * 100.0
                else:
                    active["max_return_pct"] = None
                if pd.Timestamp(active["_entry_ts"]) >= cutoff:
                    active.pop("_entry_ts", None)
                    events.append(active)
                active = None
                ready_after_exit = bool(down_seen_since_entry or st_dir == -1)
                down_seen_since_entry = False
            continue

        if has_ever_entered and not ready_after_exit:
            continue
        if op in ("STRONG_BUY", "BUY"):
            close = float(row["Close"]) if pd.notna(row.get("Close")) else float("nan")
            if not np.isfinite(close) or close <= 0:
                continue
            active = {
                "_entry_ts": pd.Timestamp(d.index[i]),
                "time": _date_text(d.index[i]),
                "opinion_code": op,
                "opinion": OPINION_LABEL[op],
                "entry_price": close,
                "adx": float(av),
                "st_value": float(stv) if pd.notna(stv) else None,
                "up_flip_st": float(flip_ref) if pd.notna(flip_ref) else None,
                "reason": reason,
                "completed": False,
                "exit_time": "",
                "exit_price": None,
                "exit_opinion": "",
                "peak_time": _date_text(d.index[i]),
                "peak_price": float(row["High"]) if pd.notna(row.get("High")) else close,
                "max_return_pct": None,
                "_peak_time": _date_text(d.index[i]),
                "_peak_price": float(row["High"]) if pd.notna(row.get("High")) else close,
            }
            has_ever_entered = True
            ready_after_exit = False
            down_seen_since_entry = False

    if active is not None and pd.Timestamp(active["_entry_ts"]) >= cutoff:
        active["peak_time"] = active.pop("_peak_time", "")
        active["peak_price"] = active.pop("_peak_price", None)
        if active.get("peak_price") is not None and active.get("entry_price"):
            active["max_return_pct"] = (float(active["peak_price"]) / float(active["entry_price"]) - 1.0) * 100.0
        active.pop("_entry_ts", None)
        events.append(active)

    completed = [
        float(e["max_return_pct"])
        for e in events
        if e.get("completed") and e.get("max_return_pct") is not None and np.isfinite(float(e["max_return_pct"]))
    ]
    return {
        "events": events,
        "median_max_return_pct": float(np.median(completed)) if completed else None,
        "completed_events": len(completed),
    }


def _safe_float(value: Any) -> float | None:
    try:
        v = float(value)
        return v if np.isfinite(v) else None
    except Exception:
        return None


def analyze(
    df: pd.DataFrame,
    period: int = SUPER_TREND_PERIOD,
    multiplier: float = SUPER_TREND_MULTIPLIER,
    market: str = "US",
    costs: dict[str, float] | None = None,
) -> dict[str, Any]:
    ohlc = _ohlc(df)
    if len(ohlc) < MIN_REQUIRED_BARS:
        return {
            "available": False,
            "reason": "insufficient_history_lt_604",
            "opinion": "HOLD",
            "opinion_code": "HOLD",
            "opinion_label": "HOLD",
            "rank_level": OPINION_ORDER["HOLD"],
        }

    signals = signal_series(ohlc, period=period, multiplier=multiplier)
    if signals.empty:
        return {
            "available": False,
            "reason": "indicator_unavailable",
            "opinion": "HOLD",
            "opinion_code": "HOLD",
            "opinion_label": "HOLD",
            "rank_level": OPINION_ORDER["HOLD"],
        }

    row = signals.iloc[-1]
    adx_value = _safe_float(row.get("ADX"))
    st_value = _safe_float(row.get("ST"))
    flip_ref = _safe_float(row.get("ST_UP_FLIP_REF"))
    flip_age = _safe_float(row.get("ST_UP_FLIP_AGE"))
    st_dir = int(row.get("ST_DIR", 0) or 0)
    if adx_value is None or st_dir == 0:
        op, reason = "HOLD", "ADX(14,14) 계산값 부족"
    else:
        op, reason = classify_supertrad_index(st_dir, adx_value, st_value, flip_ref, flip_age)

    cycles = compute_buy_cycles(signals, years=BACKTEST_YEARS)

    chart_start = max(0, len(signals) - CHART_SESSIONS)
    chart = []
    for pos in range(chart_start, len(signals)):
        idx = signals.index[pos]
        sr = signals.iloc[pos]
        vals = [_safe_float(sr.get(x)) for x in ("Open", "High", "Low", "Close")]
        if any(v is None for v in vals):
            continue
        o, h, l, c = vals
        chart.append({
            "date": pd.Timestamp(idx).date().isoformat(),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "supertrend": _safe_float(sr.get("ST")),
            "direction": int(sr.get("ST_DIR", 0) or 0),
            "adx": _safe_float(sr.get("ADX")),
            "opinion_code": str(sr.get("opinion_code") or "HOLD"),
        })

    # Only event rows in/near the chart window are needed by the mini chart;
    # the complete 2Y event list remains in research for the category report.
    chart_dates = {r["date"] for r in chart}
    chart_events = []
    for e in cycles["events"]:
        copy = dict(e)
        if copy.get("time") in chart_dates or copy.get("peak_time") in chart_dates or copy.get("exit_time") in chart_dates:
            chart_events.append(copy)

    return {
        "available": True,
        "model": "Supertrad Index",
        "period": int(period),
        "multiplier": float(multiplier),
        "adx_di_length": ADX_DI_LENGTH,
        "adx_smoothing": ADX_SMOOTHING,
        "opinion": OPINION_LABEL.get(op, op),
        "opinion_code": op,
        "opinion_label": OPINION_LABEL.get(op, op),
        "rank_level": int(OPINION_ORDER.get(op, OPINION_ORDER["HOLD"])),
        "reason": reason,
        "current_close": _safe_float(row.get("Close")),
        "st_direction": "상승" if st_dir == 1 else "하락" if st_dir == -1 else None,
        "st_value": st_value,
        "up_flip_st": flip_ref,
        "up_flip_age": flip_age,
        "adx": adx_value,
        "backtest": {
            "available": True,
            "window": "last 2 calendar years",
            "median_max_return_pct": cycles["median_max_return_pct"],
            "completed_events": int(cycles["completed_events"]),
            "event_count": int(len(cycles["events"])),
            "entry_rule": "first STRONG BUY/BUY while flat; signal-day close",
            "exit_rule": "first SELL/STRONG SELL; signal-day close",
            "median_rule": "median of completed BUY->SELL cycle peak High returns",
        },
        "chart": chart,
        "chart_events": chart_events,
        "_research": {"events": cycles["events"]},
    }
