from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# DTC v14.4.7 single-timeframe SuperTrend breakout specification.
SUPER_TREND_PERIOD = 20
SUPER_TREND_MULTIPLIER = 4.0
ADX_DI_LENGTH = 14
ADX_SMOOTHING = 14
BACKTEST_YEARS = 2
CHART_SESSIONS = 126
# The web scanner still keeps its inherited >=604-session hard filter.  The
# algorithm itself only needs enough history for ST/ADX warm-up, but keeping the
# scanner filter makes the 2Y cycle test stable and deterministic.
MIN_REQUIRED_BARS = 604

OPINION_ORDER = {
    "BUY": 0,
    "SELL": 1,
    # Legacy codes remain sortable when an old cached snapshot is restored.
    "HOLD": 1,
    "SELL_CONSIDER": 1,
    "SHORT_BUY": 0,
    "LONG_BUY": 1,
}

OPINION_LABEL = {
    "BUY": "BUY",
    "SELL": "Sell",
    "HOLD": "Sell",
    "SELL_CONSIDER": "Sell",
    "SHORT_BUY": "BUY",
    "LONG_BUY": "Sell",
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


def _pine_rma(values: np.ndarray, length: int) -> np.ndarray:
    """Pine/TradingView ta.rma() semantics for a numeric series.

    Pine seeds RMA with an SMA over the first ``length`` non-na source values,
    then applies alpha=1/length recursively.  na source values are ignored
    after a seed exists (the previous RMA is retained).
    """
    src = np.asarray(values, dtype=float)
    out = np.full(len(src), np.nan, dtype=float)
    if length <= 0 or len(src) == 0:
        return out

    finite_positions: list[int] = []
    seeded = False
    prev = np.nan
    alpha = 1.0 / float(length)

    for i, value in enumerate(src):
        if not seeded:
            if np.isfinite(value):
                finite_positions.append(i)
            if len(finite_positions) == length:
                vals = [src[j] for j in finite_positions]
                prev = float(np.mean(vals))
                out[i] = prev
                seeded = True
            continue

        if np.isfinite(value):
            prev = alpha * float(value) + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _pine_fixnan(values: np.ndarray) -> np.ndarray:
    """TradingView fixnan(): replace na with the nearest previous non-na."""
    src = np.asarray(values, dtype=float)
    out = np.full(len(src), np.nan, dtype=float)
    last = np.nan
    for i, value in enumerate(src):
        if np.isfinite(value):
            last = float(value)
        if np.isfinite(last):
            out[i] = last
    return out


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray, handle_na: bool) -> np.ndarray:
    """TradingView ta.tr / ta.tr(true) behavior."""
    n = len(close)
    tr = np.full(n, np.nan, dtype=float)
    for i in range(n):
        if i == 0 or not np.isfinite(close[i - 1]):
            if handle_na:
                tr[i] = high[i] - low[i]
            continue
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    return tr

def supertrend(
    df: pd.DataFrame,
    length: int = SUPER_TREND_PERIOD,
    multiplier: float = SUPER_TREND_MULTIPLIER,
) -> pd.DataFrame:
    """TradingView/Pine-compatible ta.supertrend() calculation.

    ATR is ta.rma(ta.tr(true), length).  The band ratchet and direction state
    follow TradingView's published Supertrend formula exactly.  DTC keeps +1
    for up and -1 for down internally (Pine's returned direction sign differs,
    but the plotted ST price series is the same).
    """
    d = _ohlc(df).copy()
    if d.empty:
        return d.assign(ATR=np.nan, ST_UPPER=np.nan, ST_LOWER=np.nan, ST=np.nan, ST_DIR=0)

    high = d["High"].to_numpy(float)
    low = d["Low"].to_numpy(float)
    close = d["Close"].to_numpy(float)
    n = len(d)

    # TradingView ta.atr(length) = ta.rma(ta.tr(true), length).
    tr = _true_range(high, low, close, handle_na=True)
    atr = _pine_rma(tr, int(length))
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
    """TradingView/Pine-compatible DMI/ADX.

    This mirrors the canonical Pine implementation used with ta.dmi():
      up      = ta.change(high)
      down    = -ta.change(low)
      plusDM  = na(up) ? na : (up > down and up > 0 ? up : 0)
      minusDM = na(down) ? na : (down > up and down > 0 ? down : 0)
      trur    = ta.rma(ta.tr, di_length)
      plus    = fixnan(100 * ta.rma(plusDM, di_length) / trur)
      minus   = fixnan(100 * ta.rma(minusDM, di_length) / trur)
      ADX     = 100 * ta.rma(abs(plus-minus)/(sum==0 ? 1 : sum), smoothing)

    Note the deliberate first-bar na in ta.change() and ta.tr (handle_na=false).
    """
    d = _ohlc(df).copy()
    n = len(d)
    if n == 0:
        for col in ("PLUS_DI", "MINUS_DI", "DX", "ADX"):
            d[col] = pd.Series(dtype=float)
        return d

    high = d["High"].to_numpy(float)
    low = d["Low"].to_numpy(float)
    close = d["Close"].to_numpy(float)

    # Pine ta.change(): first value is na.
    up = np.full(n, np.nan, dtype=float)
    down = np.full(n, np.nan, dtype=float)
    if n > 1:
        up[1:] = high[1:] - high[:-1]
        down[1:] = low[:-1] - low[1:]

    plus_dm = np.full(n, np.nan, dtype=float)
    minus_dm = np.full(n, np.nan, dtype=float)
    valid_change = np.isfinite(up) & np.isfinite(down)
    plus_dm[valid_change] = np.where(
        (up[valid_change] > down[valid_change]) & (up[valid_change] > 0.0),
        up[valid_change],
        0.0,
    )
    minus_dm[valid_change] = np.where(
        (down[valid_change] > up[valid_change]) & (down[valid_change] > 0.0),
        down[valid_change],
        0.0,
    )

    # DMI's published Pine implementation uses ta.tr (handle_na=false),
    # unlike ta.atr(), which uses ta.tr(true).
    tr = _true_range(high, low, close, handle_na=False)
    trur = _pine_rma(tr, int(di_length))
    plus_sm = _pine_rma(plus_dm, int(di_length))
    minus_sm = _pine_rma(minus_dm, int(di_length))

    plus_raw = np.full(n, np.nan, dtype=float)
    minus_raw = np.full(n, np.nan, dtype=float)
    valid_trur = np.isfinite(trur) & (trur != 0.0)
    plus_raw[valid_trur] = 100.0 * plus_sm[valid_trur] / trur[valid_trur]
    minus_raw[valid_trur] = 100.0 * minus_sm[valid_trur] / trur[valid_trur]
    plus_di = _pine_fixnan(plus_raw)
    minus_di = _pine_fixnan(minus_raw)

    denom = plus_di + minus_di
    ratio = np.full(n, np.nan, dtype=float)
    finite_di = np.isfinite(plus_di) & np.isfinite(minus_di)
    nonzero = finite_di & (denom != 0.0)
    ratio[nonzero] = np.abs(plus_di[nonzero] - minus_di[nonzero]) / denom[nonzero]
    ratio[finite_di & (denom == 0.0)] = np.abs(plus_di[finite_di & (denom == 0.0)] - minus_di[finite_di & (denom == 0.0)])

    adx_value = 100.0 * _pine_rma(ratio, int(adx_smoothing))

    d["PLUS_DI"] = plus_di
    d["MINUS_DI"] = minus_di
    d["DX"] = ratio * 100.0
    d["ADX"] = adx_value
    return d



def add_up_flip_reference(
    df: pd.DataFrame,
    dir_col: str = "ST_DIR",
    st_col: str = "ST",
    ref_col: str = "ST_UP_FLIP_REF",
    age_col: str = "ST_UP_FLIP_AGE",
) -> pd.DataFrame:
    """Attach the previous DOWN ST price to each UP leg.

    The flip bar itself has age 0, so the gate is deliberately unavailable on
    that bar.  From the next bar onward the current UP ST may qualify once it
    reaches/exceeds the final DOWN ST immediately before the flip.
    """
    d = df.copy()
    n = len(d)
    refs = np.full(n, np.nan, dtype=float)
    ages = np.full(n, np.nan, dtype=float)
    current_ref = np.nan
    current_age = -1
    dirs = pd.to_numeric(d.get(dir_col, pd.Series(index=d.index, dtype=float)), errors="coerce").fillna(0).astype(int).to_numpy()
    sts = pd.to_numeric(d.get(st_col, pd.Series(index=d.index, dtype=float)), errors="coerce").to_numpy(dtype=float)
    for i in range(n):
        is_flip = i > 0 and dirs[i] == 1 and dirs[i - 1] == -1 and np.isfinite(sts[i - 1])
        if is_flip:
            current_ref = float(sts[i - 1])
            current_age = 0
        if dirs[i] == 1 and np.isfinite(current_ref):
            refs[i] = current_ref
            ages[i] = float(current_age)
            current_age += 1
        elif dirs[i] != 1:
            current_age = -1
    d[ref_col] = refs
    d[age_col] = ages
    return d


def _gate(direction: int, st_value: Any, ref_value: Any, age: Any = None) -> bool:
    """Single daily ST(20,4) breakout gate.

    BUY is true whenever the current SuperTrend is UP and its ST value has
    reached or exceeded the final ST value of the immediately preceding DOWN
    leg.  The flip bar itself is allowed to qualify if it already satisfies the
    price-level comparison.
    """
    try:
        return (
            int(direction) == 1
            and np.isfinite(float(st_value))
            and np.isfinite(float(ref_value))
            and float(st_value) >= float(ref_value)
        )
    except Exception:
        return False


def classify_supertrend(
    direction: int,
    st_value: Any,
    ref_value: Any,
    age: Any = None,
) -> tuple[str, str, bool]:
    """DTC single-SuperTrend opinion table.

    BUY: current daily ST(20,4) is UP and current ST >= previous DOWN leg's
    final ST value.  Every other state is Sell.
    """
    buy = _gate(direction, st_value, ref_value, age)
    if buy:
        return "BUY", "ST(20,4) 상승 · 직전 하락 ST 마지막 값 돌파", True
    return "SELL", "ST(20,4) BUY 돌파 조건 불충족", False


def signal_series(
    df: pd.DataFrame,
    period: int = SUPER_TREND_PERIOD,
    multiplier: float = SUPER_TREND_MULTIPLIER,
    di_length: int = ADX_DI_LENGTH,
    adx_smoothing: int = ADX_SMOOTHING,
) -> pd.DataFrame:
    daily_st = supertrend(df, period, multiplier)
    if daily_st.empty:
        return daily_st
    daily_adx = adx(daily_st[["Open", "High", "Low", "Close", "Volume"]], di_length, adx_smoothing)
    for col in ("ATR", "ST_UPPER", "ST_LOWER", "ST", "ST_DIR"):
        daily_adx[col] = daily_st[col]
    daily_adx = add_up_flip_reference(daily_adx)

    ops, labels, reasons, buy_flags = [], [], [], []
    for _, row in daily_adx.iterrows():
        op, reason, buy = classify_supertrend(
            int(row.get("ST_DIR", 0) or 0),
            row.get("ST"),
            row.get("ST_UP_FLIP_REF"),
            row.get("ST_UP_FLIP_AGE"),
        )
        ops.append(op)
        labels.append(OPINION_LABEL[op])
        reasons.append(reason)
        buy_flags.append(bool(buy))
    daily_adx["opinion_code"] = ops
    daily_adx["opinion"] = labels
    daily_adx["reason"] = reasons
    # CASE1 is retained as a compatibility alias for the current BUY gate.
    daily_adx["CASE1"] = buy_flags
    return daily_adx


def _date_text(ts: Any) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def compute_buy_cycles(enriched: pd.DataFrame, years: int = BACKTEST_YEARS) -> dict[str, Any]:
    """Backtest the single ST(20,4) breakout BUY rule.

    Entry is the first BUY while flat at signal-day Close. Exit is the first
    subsequent Sell at signal-day Close. The headline result is the median of
    each completed cycle's maximum High return from entry through exit.
    """
    if enriched is None or enriched.empty:
        return {"events": [], "median_max_return_pct": None, "completed_events": 0}
    d = enriched.copy()
    idx = pd.to_datetime(d.index)
    cutoff = idx.max() - pd.DateOffset(years=years)
    events: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None

    for i, (_, row) in enumerate(d.iterrows()):
        op = str(row.get("opinion_code") or "HOLD")
        if active is not None:
            hi = row.get("High", np.nan)
            if pd.notna(hi) and np.isfinite(float(hi)) and float(hi) > float(active["_peak_price"]):
                active["_peak_price"] = float(hi)
                active["_peak_time"] = _date_text(d.index[i])
            if op == "SELL":
                active["completed"] = True
                active["exit_time"] = _date_text(d.index[i])
                active["exit_price"] = float(row["Close"]) if pd.notna(row.get("Close")) else None
                active["peak_time"] = active.pop("_peak_time", "")
                active["peak_price"] = active.pop("_peak_price", None)
                active["max_return_pct"] = (
                    (float(active["peak_price"]) / float(active["entry_price"]) - 1.0) * 100.0
                    if active.get("peak_price") is not None and active.get("entry_price") else None
                )
                if pd.Timestamp(active["_entry_ts"]) >= cutoff:
                    active.pop("_entry_ts", None)
                    events.append(active)
                active = None
            continue

        if op == "BUY":
            close = float(row["Close"]) if pd.notna(row.get("Close")) else float("nan")
            if not np.isfinite(close) or close <= 0:
                continue
            high = float(row["High"]) if pd.notna(row.get("High")) else close
            active = {
                "_entry_ts": pd.Timestamp(d.index[i]),
                "time": _date_text(d.index[i]),
                "opinion_code": "BUY",
                "opinion": "BUY",
                "entry_price": close,
                "completed": False,
                "exit_time": "",
                "exit_price": None,
                "_peak_time": _date_text(d.index[i]),
                "_peak_price": high,
            }

    if active is not None and pd.Timestamp(active["_entry_ts"]) >= cutoff:
        active["peak_time"] = active.pop("_peak_time", "")
        active["peak_price"] = active.pop("_peak_price", None)
        active["max_return_pct"] = (
            (float(active["peak_price"]) / float(active["entry_price"]) - 1.0) * 100.0
            if active.get("peak_price") is not None and active.get("entry_price") else None
        )
        active.pop("_entry_ts", None)
        events.append(active)

    completed = [
        float(e["max_return_pct"]) for e in events
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


def _current_case1_age(signals: pd.DataFrame) -> tuple[str | None, int | None, int | None]:
    """Return current CASE1 run start date, calendar days and trading sessions.

    The first day CASE1 becomes true is +0. Calendar-day age is shown to the
    user; trading-session age is retained as useful diagnostic metadata.
    """
    if signals is None or signals.empty or "CASE1" not in signals.columns:
        return None, None, None
    flags = signals["CASE1"].fillna(False).astype(bool).to_numpy()
    if len(flags) == 0 or not bool(flags[-1]):
        return None, None, None
    start = len(flags) - 1
    while start > 0 and bool(flags[start - 1]):
        start -= 1
    start_ts = pd.Timestamp(signals.index[start]).normalize()
    now_ts = pd.Timestamp(signals.index[-1]).normalize()
    return start_ts.date().isoformat(), max(0, int((now_ts - start_ts).days)), int(len(flags) - 1 - start)


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
            "available": False, "reason": "insufficient_history_lt_604",
            "opinion": "Sell", "opinion_code": "SELL", "opinion_label": "Sell",
            "rank_level": OPINION_ORDER["SELL"],
        }
    signals = signal_series(ohlc, period=period, multiplier=multiplier)
    if signals.empty:
        return {
            "available": False, "reason": "indicator_unavailable",
            "opinion": "Sell", "opinion_code": "SELL", "opinion_label": "Sell",
            "rank_level": OPINION_ORDER["SELL"],
        }
    row = signals.iloc[-1]
    op = str(row.get("opinion_code") or "SELL")
    reason = str(row.get("reason") or "")
    buy_signal_date, buy_age_days, buy_age_sessions = _current_case1_age(signals) if op == "BUY" else (None, None, None)
    opinion_label = f"BUY (+{buy_age_days})" if op == "BUY" and buy_age_days is not None else OPINION_LABEL.get(op, op)
    adx_value = _safe_float(row.get("ADX"))
    d_st = _safe_float(row.get("ST"))
    d_ref = _safe_float(row.get("ST_UP_FLIP_REF"))
    d_dir = int(row.get("ST_DIR", 0) or 0)
    cycles = compute_buy_cycles(signals, years=BACKTEST_YEARS)

    chart_start = max(0, len(signals) - CHART_SESSIONS)
    chart = []
    for pos in range(chart_start, len(signals)):
        idx = signals.index[pos]; sr = signals.iloc[pos]
        vals = [_safe_float(sr.get(x)) for x in ("Open", "High", "Low", "Close")]
        if any(v is None for v in vals):
            continue
        o, h, l, c = vals
        chart.append({
            "date": pd.Timestamp(idx).date().isoformat(),
            "open": o, "high": h, "low": l, "close": c,
            "supertrend": _safe_float(sr.get("ST")),
            "direction": int(sr.get("ST_DIR", 0) or 0),
            "adx": _safe_float(sr.get("ADX")),
            "opinion_code": str(sr.get("opinion_code") or "HOLD"),
        })

    chart_dates = {r["date"] for r in chart}
    chart_events = []
    for e in cycles["events"]:
        if e.get("time") in chart_dates or e.get("peak_time") in chart_dates or e.get("exit_time") in chart_dates:
            chart_events.append(dict(e))

    result = {
        "available": True,
        "model": "SuperTrend(20,4) Breakout",
        "period": int(period), "multiplier": float(multiplier),
        "adx_di_length": ADX_DI_LENGTH, "adx_smoothing": ADX_SMOOTHING,
        "opinion": opinion_label, "opinion_code": op, "opinion_label": opinion_label,
        "rank_level": int(OPINION_ORDER.get(op, OPINION_ORDER["SELL"])),
        "buy_signal_date": buy_signal_date,
        "buy_age_days": buy_age_days,
        "buy_age_sessions": buy_age_sessions,
        "reason": reason,
        "current_close": _safe_float(row.get("Close")),
        "st_d_direction": "상승" if d_dir == 1 else "하락" if d_dir == -1 else None,
        "st_d_value": d_st,
        "st_reference_value": d_ref,
        "case1": bool(row.get("CASE1", False)),
        "adx": adx_value,
        "backtest": {
            "available": True,
            "window": "last 2 calendar years",
            "median_max_return_pct": cycles["median_max_return_pct"],
            "completed_events": int(cycles["completed_events"]),
            "event_count": int(len(cycles["events"])),
            "entry_rule": "first ST(20,4) breakout BUY while flat; signal-day close",
            "exit_rule": "first subsequent Sell; signal-day close",
            "median_rule": "median of completed BUY->Sell cycle peak High returns",
        },
        "chart": chart,
        "chart_events": chart_events,
        "_research": {"events": cycles["events"]},
    }
    return result
