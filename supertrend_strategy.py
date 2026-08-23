from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

SUPER_TREND_PERIOD = 10
SUPER_TREND_MULTIPLIER = 3.0
WARMUP_DISCARD_BARS = 100
BACKTEST_YEARS = 2
BACKTEST_SESSIONS = 504
MIN_REQUIRED_BARS = 604
CHART_SESSIONS = 126
NEW_SELL_WINDOW_BARS = 5
STRONG_BUY_GATE_BARS = 3  # gate bar=0, then +1/+2/+3 trading bars

OPINION_ORDER = {
    "STRONG_BUY": 0,
    "BUY": 1,
    "HOLD": 2,
    "SELL": 3,
}

OPINION_LABEL = {
    "STRONG_BUY": "강한 매수",
    "BUY": "매수",
    "HOLD": "Hold",
    "SELL": "매도",
}


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype(float)


def _ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Return valid adjusted OHLC bars only; never forward-fill missing sessions."""
    required = ["Open", "High", "Low", "Close"]
    if df is None or df.empty or any(c not in df.columns for c in required):
        return pd.DataFrame(columns=required)
    out = pd.DataFrame(index=pd.DatetimeIndex(df.index))
    for c in required:
        out[c] = _num(df[c]).to_numpy()
    out["Volume"] = _num(df["Volume"]).to_numpy() if "Volume" in df.columns else 0.0
    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.dropna(subset=required)
    out = out[
        (out["High"] >= out["Low"])
        & (out["Open"] > 0)
        & (out["High"] > 0)
        & (out["Low"] > 0)
        & (out["Close"] > 0)
    ]
    out["Volume"] = out["Volume"].fillna(0.0).clip(lower=0.0)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """TradingView/Pine Wilder RMA: SMA seed then alpha=1/period recurrence."""
    values = pd.to_numeric(series, errors="coerce").to_numpy(float)
    out = np.full(len(values), np.nan, dtype=float)
    if period <= 0 or len(values) < period:
        return pd.Series(out, index=series.index, dtype=float)
    for seed_end in range(period - 1, len(values)):
        seed = values[seed_end - period + 1 : seed_end + 1]
        if np.all(np.isfinite(seed)):
            out[seed_end] = float(np.mean(seed))
            start = seed_end + 1
            break
    else:
        return pd.Series(out, index=series.index, dtype=float)
    for i in range(start, len(values)):
        if np.isfinite(values[i]) and np.isfinite(out[i - 1]):
            out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return pd.Series(out, index=series.index, dtype=float)


def true_range(df: pd.DataFrame) -> pd.Series:
    ohlc = _ohlc(df)
    if ohlc.empty:
        return pd.Series(dtype=float)
    high, low, close = ohlc["High"], ohlc["Low"], ohlc["Close"]
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def supertrend(
    df: pd.DataFrame,
    period: int = SUPER_TREND_PERIOD,
    multiplier: float = SUPER_TREND_MULTIPLIER,
) -> pd.DataFrame:
    """TradingView ta.supertrend(factor, atrPeriod)-compatible line values.

    Internal direction convention is +1=UP, -1=DOWN (TradingView returns the
    opposite sign). Inputs are standard adjusted OHLC, ATR uses Wilder RMA,
    and the state machine is a direct translation of TradingView's documented
    pine_supertrend() reference implementation.
    """
    ohlc = _ohlc(df)
    columns = [
        "tr", "atr", "hl2", "upper_basic", "lower_basic",
        "upper", "lower", "supertrend", "direction",
    ]
    if ohlc.empty:
        return pd.DataFrame(index=getattr(df, "index", None), columns=columns, dtype=float)

    high, low, close = ohlc["High"], ohlc["Low"], ohlc["Close"]
    tr = true_range(ohlc).reindex(ohlc.index)
    atr = wilder_rma(tr, int(period))
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + float(multiplier) * atr
    lower_basic = hl2 - float(multiplier) * atr

    n = len(ohlc)
    upper = np.full(n, np.nan, dtype=float)
    lower = np.full(n, np.nan, dtype=float)
    direction = np.full(n, np.nan, dtype=float)
    st = np.full(n, np.nan, dtype=float)
    a = atr.to_numpy(float)
    c = close.to_numpy(float)
    ub0 = upper_basic.to_numpy(float)
    lb0 = lower_basic.to_numpy(float)

    for i in range(n):
        if not np.isfinite(a[i]):
            continue
        # Pine uses nz(previous final band); on the first ATR-valid bar this
        # reduces to the current basic bands.
        if i == 0 or not np.isfinite(upper[i - 1]) or not np.isfinite(lower[i - 1]):
            upper[i] = ub0[i]
            lower[i] = lb0[i]
        else:
            prev_upper, prev_lower = upper[i - 1], lower[i - 1]
            prev_close = c[i - 1]
            lower[i] = lb0[i] if (lb0[i] > prev_lower or prev_close < prev_lower) else prev_lower
            upper[i] = ub0[i] if (ub0[i] < prev_upper or prev_close > prev_upper) else prev_upper

        # TradingView reference: direction=DOWN while previous ATR is na;
        # thereafter state depends on which final band held prevSuperTrend.
        if i == 0 or not np.isfinite(a[i - 1]) or not np.isfinite(st[i - 1]):
            direction[i] = -1.0
        else:
            prev_on_upper = np.isclose(st[i - 1], upper[i - 1], rtol=1e-12, atol=1e-12)
            if prev_on_upper:  # previous state DOWN
                direction[i] = 1.0 if c[i] > upper[i] else -1.0
            else:  # previous state UP
                direction[i] = -1.0 if c[i] < lower[i] else 1.0
        st[i] = lower[i] if direction[i] > 0 else upper[i]

    return pd.DataFrame(
        index=ohlc.index,
        data={
            "tr": tr.to_numpy(float),
            "atr": atr.to_numpy(float),
            "hl2": hl2.to_numpy(float),
            "upper_basic": upper_basic.to_numpy(float),
            "lower_basic": lower_basic.to_numpy(float),
            "upper": upper,
            "lower": lower,
            "supertrend": st,
            "direction": direction,
        },
    )


def _opinion_for(
    close: float,
    p0: float,
    p1: float,
    direction: float,
    bars_since_gate: int | None,
) -> tuple[str, str | None, float | None]:
    """Opinion rule: Strong Buy for gate day through +3 trading bars."""
    if not np.isfinite(direction):
        return "HOLD", "NO_FLIP", None
    if direction < 0:
        return "SELL", None, None
    if not (np.isfinite(p0) and p0 > 0 and np.isfinite(p1)):
        return "HOLD", "NO_FLIP", None

    r_pct = (close - p0) / p0 * 100.0 if np.isfinite(close) and close > 0 else np.nan
    if p1 < p0:
        return "HOLD", "BELOW_GATE", float(r_pct) if np.isfinite(r_pct) else None

    eps = max(1e-12, abs(close) * 1e-12)
    assert close + eps >= p1 >= p0 - eps, f"gate invariant failed: close={close}, p1={p1}, p0={p0}"
    assert np.isfinite(r_pct) and r_pct >= -1e-10, f"r_pct invalid after gate: {r_pct}"
    age = int(bars_since_gate) if bars_since_gate is not None else 0
    if 0 <= age <= STRONG_BUY_GATE_BARS:
        return "STRONG_BUY", None, max(0.0, float(r_pct))
    return "BUY", None, max(0.0, float(r_pct))


def signal_series(
    df: pd.DataFrame,
    period: int = SUPER_TREND_PERIOD,
    multiplier: float = SUPER_TREND_MULTIPLIER,
    warmup_discard: int = WARMUP_DISCARD_BARS,
) -> pd.DataFrame:
    ohlc = _ohlc(df)
    st = supertrend(ohlc, period=period, multiplier=multiplier)
    out = ohlc.join(st, how="left")
    n = len(out)
    if n == 0:
        return out

    st_arr = out["supertrend"].to_numpy(float)
    dir_arr = out["direction"].to_numpy(float)
    close_arr = out["Close"].to_numpy(float)
    atr_arr = out["atr"].to_numpy(float)
    first_valids = np.flatnonzero(np.isfinite(st_arr) & np.isfinite(dir_arr))
    first_valid = int(first_valids[0]) if len(first_valids) else n
    decision_from = first_valid + int(warmup_discard)

    p0_values = np.full(n, np.nan)
    flip_positions = np.full(n, np.nan)
    gate_positions = np.full(n, np.nan)
    r_gate_values = np.full(n, np.nan)
    r_values = np.full(n, np.nan)
    stop_values = np.full(n, np.nan)
    atr_pct_values = np.full(n, np.nan)
    g_atr_values = np.full(n, np.nan)
    d_atr_values = np.full(n, np.nan)
    bars_flip_values = np.full(n, np.nan)
    bars_gate_values = np.full(n, np.nan)
    sell_flip_values = np.full(n, np.nan)
    new_sell_values = np.zeros(n, dtype=bool)
    decision_valid = np.zeros(n, dtype=bool)
    opinion_codes: list[str] = ["HOLD"] * n
    hold_reasons: list[str | None] = ["NO_FLIP"] * n
    leg_ids = np.full(n, np.nan)

    current_p0 = np.nan
    current_flip: int | None = None
    current_gate: int | None = None
    current_r_at_gate = np.nan
    current_leg_id = 0
    last_sell_flip: int | None = None

    for i in range(n):
        d, p1, close, atr = dir_arr[i], st_arr[i], close_arr[i], atr_arr[i]
        prev_d = dir_arr[i - 1] if i > 0 else np.nan
        decision_valid[i] = i >= decision_from and np.isfinite(d) and np.isfinite(p1)

        if np.isfinite(d) and d > 0 and i > 0 and np.isfinite(prev_d) and prev_d < 0:
            current_leg_id += 1
            current_flip = i
            current_gate = None
            current_r_at_gate = np.nan
            current_p0 = st_arr[i - 1] if np.isfinite(st_arr[i - 1]) else np.nan

        if np.isfinite(d) and d < 0 and i > 0 and np.isfinite(prev_d) and prev_d > 0:
            last_sell_flip = i
            current_flip = None
            current_gate = None
            current_r_at_gate = np.nan
            current_p0 = np.nan

        if np.isfinite(d) and d > 0:
            if current_flip is not None:
                flip_positions[i] = current_flip
                leg_ids[i] = current_leg_id
                bars_flip_values[i] = i - current_flip
            if np.isfinite(current_p0):
                p0_values[i] = current_p0
                r_values[i] = (close - current_p0) / current_p0 * 100.0
                if np.isfinite(atr) and atr > 0:
                    g_atr_values[i] = (close - current_p0) / atr
                if np.isfinite(p1) and p1 >= current_p0 and current_gate is None:
                    current_gate = i
                    current_r_at_gate = r_values[i]
                if current_gate is not None:
                    gate_positions[i] = current_gate
                    bars_gate_values[i] = i - current_gate
                    r_gate_values[i] = current_r_at_gate

            gate_age = int(i - current_gate) if current_gate is not None else None
            op, reason, r = _opinion_for(close, current_p0, p1, d, gate_age)
            opinion_codes[i] = op
            hold_reasons[i] = reason if op == "HOLD" else None
            if r is not None and np.isfinite(r):
                r_values[i] = float(r)
        elif np.isfinite(d) and d < 0:
            opinion_codes[i] = "SELL"
            hold_reasons[i] = None
        else:
            opinion_codes[i] = "HOLD"
            hold_reasons[i] = "NO_FLIP"

        if np.isfinite(close) and close > 0 and np.isfinite(p1):
            stop_values[i] = (close - p1) / close * 100.0
        if np.isfinite(close) and close > 0 and np.isfinite(atr) and atr > 0:
            atr_pct_values[i] = atr / close * 100.0
            d_atr_values[i] = (close - p1) / atr if np.isfinite(p1) else np.nan

        if last_sell_flip is not None and np.isfinite(d) and d < 0:
            sell_flip_values[i] = last_sell_flip
            new_sell_values[i] = (i - last_sell_flip) < NEW_SELL_WINDOW_BARS

    out["p0"] = p0_values
    out["flip_pos"] = flip_positions
    out["gate_pos"] = gate_positions
    out["leg_id"] = leg_ids
    out["r_at_gate"] = r_gate_values
    out["r_pct"] = r_values
    out["stop_pct"] = stop_values
    out["atr_pct"] = atr_pct_values
    out["g_atr"] = g_atr_values
    out["d_atr"] = d_atr_values
    out["bars_since_flip"] = bars_flip_values
    out["bars_since_gate"] = bars_gate_values
    out["sell_flip_pos"] = sell_flip_values
    out["new_sell"] = new_sell_values
    out["decision_valid"] = decision_valid
    out["opinion_code"] = opinion_codes
    out["opinion"] = [OPINION_LABEL[x] for x in opinion_codes]
    out["hold_reason"] = hold_reasons
    return out


def _rsi14(close: pd.Series) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = wilder_rma(gain, 14)
    avg_loss = wilder_rma(loss, 14)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(avg_loss > 0, 100.0)
    return rsi


def reference_setups(ohlc: pd.DataFrame, current_direction: float | None) -> dict[str, Any]:
    """Reference-only breakout/pullback labels. Never used by opinion/ranking."""
    df = _ohlc(ohlc)
    if len(df) < 60:
        neutral = {"grade": "NORMAL", "label": "보통", "reason": "데이터 부족"}
        return {"reference_only": True, "breakout": dict(neutral), "pullback": dict(neutral)}

    close = df["Close"]
    volume = df["Volume"]
    prior_high20 = df["High"].shift(1).rolling(20, min_periods=20).max().iloc[-1]
    prior_vol20 = volume.shift(1).rolling(20, min_periods=20).mean().iloc[-1]
    c = float(close.iloc[-1])
    v = float(volume.iloc[-1])
    dist_high = (c / prior_high20 - 1.0) * 100.0 if np.isfinite(prior_high20) and prior_high20 > 0 else np.nan
    vol_ratio = v / prior_vol20 if np.isfinite(prior_vol20) and prior_vol20 > 0 else np.nan

    if np.isfinite(dist_high) and np.isfinite(vol_ratio):
        if dist_high >= 0.0 and vol_ratio >= 1.20:
            b_grade, b_label = "GOOD", "좋음"
        elif dist_high >= -3.0 and vol_ratio >= 0.80:
            b_grade, b_label = "NORMAL", "보통"
        else:
            b_grade, b_label = "BAD", "나쁨"
    else:
        b_grade, b_label = "NORMAL", "보통"

    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    rsi14 = _rsi14(close).iloc[-1]
    ema20_dist = (c / ema20 - 1.0) * 100.0 if np.isfinite(ema20) and ema20 > 0 else np.nan
    is_up = current_direction is not None and np.isfinite(current_direction) and current_direction > 0
    if is_up and np.isfinite(ema20_dist) and np.isfinite(rsi14) and np.isfinite(ema50):
        if ema20 >= ema50 and -2.0 <= ema20_dist <= 3.0 and 40.0 <= rsi14 <= 60.0:
            p_grade, p_label = "GOOD", "좋음"
        elif ema20 >= ema50 * 0.98 and -5.0 <= ema20_dist <= 6.0 and 35.0 <= rsi14 <= 70.0:
            p_grade, p_label = "NORMAL", "보통"
        else:
            p_grade, p_label = "BAD", "나쁨"
    else:
        p_grade, p_label = "BAD", "나쁨"

    return {
        "reference_only": True,
        "breakout": {
            "grade": b_grade,
            "label": b_label,
            "distance_to_prior_20d_high_pct": float(dist_high) if np.isfinite(dist_high) else None,
            "volume_ratio_vs_prior_20d": float(vol_ratio) if np.isfinite(vol_ratio) else None,
            "rule": "GOOD=20D high breakout + volume>=1.2x; NORMAL=within 3% + volume>=0.8x",
        },
        "pullback": {
            "grade": p_grade,
            "label": p_label,
            "ema20_distance_pct": float(ema20_dist) if np.isfinite(ema20_dist) else None,
            "rsi14": float(rsi14) if np.isfinite(rsi14) else None,
            "ema20": float(ema20) if np.isfinite(ema20) else None,
            "ema50": float(ema50) if np.isfinite(ema50) else None,
            "rule": "GOOD=ST up + EMA20>=EMA50 + near EMA20 + RSI40~60; NORMAL=looser band",
        },
    }


def backtest_strong_buy_stats(
    df: pd.DataFrame,
    signals: pd.DataFrame | None = None,
    sessions: int = BACKTEST_SESSIONS,
) -> dict[str, Any]:
    """2Y Strong-Buy diagnostics.

    For each rising leg, only the first STRONG_BUY signal is sampled. Entry is
    the next-bar open. Two headline metrics are produced:
      1) median of each trade's maximum gross return before the next Sell signal
      2) 20-session win rate, where Close[entry+20] > entry open is a win

    Recent samples without a completed Sell can still contribute to the 20-day
    metric when 20 future sessions exist, but are excluded from max-return median.
    """
    signals = signal_series(df) if signals is None else signals
    if signals.empty or len(signals) < MIN_REQUIRED_BARS:
        return {
            "available": False,
            "reason": "insufficient_history_lt_604",
            "max_return_median_pct": None,
            "win_rate_20d_pct": None,
            "max_return_samples": 0,
            "win_20d_samples": 0,
        }

    n = len(signals)
    dates = pd.DatetimeIndex(signals.index)
    last_ts = pd.Timestamp(dates[-1])
    try:
        cutoff = last_ts - pd.DateOffset(years=BACKTEST_YEARS)
        in_window = np.asarray(dates >= cutoff, dtype=bool)
    except Exception:
        in_window = np.arange(n) >= max(0, n - sessions)
    in_window &= signals["decision_valid"].fillna(False).to_numpy(bool)

    opinions = signals["opinion_code"].astype(str).to_numpy()
    direction = signals["direction"].to_numpy(float)
    leg_ids = signals["leg_id"].to_numpy(float)
    opens = signals["Open"].to_numpy(float)
    highs = signals["High"].to_numpy(float)
    closes = signals["Close"].to_numpy(float)

    # Resolve the first Strong-Buy entry per rising leg.
    entries: list[dict[str, Any]] = []
    entered_legs: set[int] = set()
    for i in range(n):
        if not in_window[i] or opinions[i] != "STRONG_BUY" or not np.isfinite(leg_ids[i]):
            continue
        leg = int(leg_ids[i])
        if leg in entered_legs or i + 1 >= n or not (np.isfinite(opens[i + 1]) and opens[i + 1] > 0):
            continue
        entered_legs.add(leg)
        entries.append({
            "leg_id": leg,
            "signal_pos": int(i),
            "entry_pos": int(i + 1),
            "signal_date": dates[i].date().isoformat(),
            "entry_date": dates[i + 1].date().isoformat(),
            "entry_price": float(opens[i + 1]),
        })

    max_returns: list[float] = []
    win20_flags: list[bool] = []
    trades: list[dict[str, Any]] = []

    for entry in entries:
        ep = int(entry["entry_pos"])
        entry_price = float(entry["entry_price"])

        # First UP->DOWN transition after entry; max return is measured only to
        # that Sell signal bar, consistent with the strategy life-cycle.
        sell_pos = None
        for j in range(max(ep, 1), n):
            if (
                np.isfinite(direction[j - 1]) and np.isfinite(direction[j])
                and direction[j - 1] > 0 and direction[j] < 0
            ):
                sell_pos = j
                break

        max_return_pct = None
        if sell_pos is not None and sell_pos >= ep:
            seg = highs[ep : sell_pos + 1]
            if len(seg) and np.isfinite(seg).any():
                max_high = float(np.nanmax(seg))
                max_return_pct = (max_high / entry_price - 1.0) * 100.0
                if np.isfinite(max_return_pct):
                    max_returns.append(float(max_return_pct))

        win20 = None
        pos20 = ep + 20
        if pos20 < n and np.isfinite(closes[pos20]) and closes[pos20] > 0:
            win20 = bool(closes[pos20] > entry_price)
            win20_flags.append(win20)

        trades.append({
            **entry,
            "sell_signal_date": dates[sell_pos].date().isoformat() if sell_pos is not None else None,
            "max_return_pct": float(max_return_pct) if max_return_pct is not None else None,
            "return_20d_pct": float((closes[pos20] / entry_price - 1.0) * 100.0) if pos20 < n and np.isfinite(closes[pos20]) else None,
            "win_20d": win20,
        })

    daily = []
    valid_positions = np.flatnonzero(signals["decision_valid"].fillna(False).to_numpy(bool))
    for i in valid_positions[-60:]:
        daily.append({"date": dates[i].date().isoformat(), "opinion_code": str(opinions[i])})

    return {
        "available": True,
        "window": "last 2 calendar years",
        "max_return_median_pct": float(np.median(max_returns)) if max_returns else None,
        "win_rate_20d_pct": float(np.mean(win20_flags) * 100.0) if win20_flags else None,
        "max_return_samples": int(len(max_returns)),
        "win_20d_samples": int(len(win20_flags)),
        "entry_rule": "first Strong Buy per rising leg; next bar open",
        "max_return_rule": "maximum daily High return from entry until next Sell signal",
        "win_20d_rule": "Close 20 sessions after entry is above entry open",
        "_research": {"trades": trades, "daily_opinions": daily},
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
            "opinion": "Hold",
            "opinion_code": "HOLD",
            "opinion_label": "Hold",
            "hold_reason": "NO_FLIP",
            "rank_level": OPINION_ORDER["HOLD"],
        }

    signals = signal_series(ohlc, period=period, multiplier=multiplier)
    valid = signals[signals["decision_valid"].fillna(False)]
    if valid.empty:
        return {
            "available": False,
            "reason": "warmup_not_completed",
            "opinion": "Hold",
            "opinion_code": "HOLD",
            "opinion_label": "Hold",
            "hold_reason": "NO_FLIP",
            "rank_level": OPINION_ORDER["HOLD"],
        }

    row = valid.iloc[-1]
    current_pos = signals.index.get_loc(valid.index[-1])
    direction = _safe_float(row.get("direction"))
    p1 = _safe_float(row.get("supertrend"))
    p0 = _safe_float(row.get("p0"))
    close = _safe_float(row.get("Close"))
    opinion_code = str(row.get("opinion_code") or "HOLD")
    opinion_label = OPINION_LABEL.get(opinion_code, "Hold")
    hold_reason_raw = row.get("hold_reason")
    hold_reason = str(hold_reason_raw) if opinion_code == "HOLD" and isinstance(hold_reason_raw, str) and hold_reason_raw else None
    new_sell = bool(row.get("new_sell", False)) if opinion_code == "SELL" else False

    backtest = backtest_strong_buy_stats(ohlc, signals=signals)
    research = dict(backtest.pop("_research", {}) or {})
    refs = reference_setups(ohlc, direction)

    chart_start = max(0, len(signals) - CHART_SESSIONS)
    chart = []
    for pos in range(chart_start, len(signals)):
        idx = signals.index[pos]
        sr = signals.iloc[pos]
        vals = [_safe_float(sr.get(x)) for x in ("Open", "High", "Low", "Close")]
        if any(v is None for v in vals):
            continue
        o, h, l, c = vals
        stv = _safe_float(sr.get("supertrend"))
        d = int(sr["direction"]) if np.isfinite(sr.get("direction", np.nan)) else None
        chart.append({
            "date": pd.Timestamp(idx).date().isoformat(),
            "open": o, "high": h, "low": l, "close": c,
            "supertrend": stv,
            "direction": d,
        })

    bars_since_sell_flip = None
    if opinion_code == "SELL" and np.isfinite(row.get("sell_flip_pos", np.nan)):
        bars_since_sell_flip = int(current_pos - int(row["sell_flip_pos"]))

    return {
        "available": bool(p1 is not None and direction is not None),
        "period": int(period),
        "multiplier": float(multiplier),
        "engine": "TradingView ta.supertrend compatible",
        "atr_smoothing": "Wilder RMA",
        "warmup_discard_bars": int(WARMUP_DISCARD_BARS),
        "strong_buy_gate_bars": int(STRONG_BUY_GATE_BARS),
        "direction": "UP" if direction is not None and direction > 0 else "DOWN" if direction is not None else None,
        "opinion": opinion_label,
        "opinion_code": opinion_code,
        "opinion_label": opinion_label,
        "hold_reason": hold_reason,
        "rank_level": int(OPINION_ORDER.get(opinion_code, OPINION_ORDER["HOLD"])),
        "current_close": close,
        "new_sell": new_sell,
        "bars_since_sell_flip": bars_since_sell_flip,
        "reference_setups": refs,
        "backtest": backtest,
        "chart": chart,
        "_research": research,
    }
