from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

SUPER_TREND_PERIOD = 10
SUPER_TREND_MULTIPLIER = 2.0
WARMUP_DISCARD_BARS = 100
BACKTEST_YEARS = 2
BACKTEST_SESSIONS = 504
MIN_REQUIRED_BARS = 604  # ~2Y test + 100-bar post-ATR warm-up discard
CHART_SESSIONS = 126     # ~6 trading months
NEW_SELL_WINDOW_BARS = 5

# Internal stable codes. Public JSON also exposes the Korean opinion label required
# by the strategy specification.
OPINION_ORDER = {
    "BUY_S": 0,
    "BUY_A": 1,
    "BUY_B": 2,
    "BUY_C": 3,
    "HOLD": 4,
    "SELL": 5,
}

OPINION_LABEL = {
    "BUY_S": "매수S",
    "BUY_A": "매수A",
    "BUY_B": "매수B",
    "BUY_C": "매수C",
    "HOLD": "Hold",
    "SELL": "매도",
}

GRADE_LABEL = {
    "BUY_S": "S",
    "BUY_A": "A",
    "BUY_B": "B",
    "BUY_C": "C",
    "HOLD_OVEREXTENDED": "Hold-OVEREXTENDED",
}

DEFAULT_COSTS = {
    "fee_pct_per_side": 0.015,
    "slippage_pct_per_side": 0.10,
    "kr_sell_tax_pct": 0.18,
    "us_sell_tax_pct": 0.0,
}


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype(float)


def _ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Return valid OHLC bars only; never forward-fill missing/suspended sessions."""
    required = ["Open", "High", "Low", "Close"]
    if df is None or df.empty or any(c not in df.columns for c in required):
        return pd.DataFrame(columns=required)
    out = pd.DataFrame(index=pd.DatetimeIndex(df.index))
    for c in required:
        out[c] = _num(df[c]).to_numpy()
    if "Volume" in df.columns:
        out["Volume"] = _num(df["Volume"]).to_numpy()
    else:
        out["Volume"] = 0.0
    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.dropna(subset=required)
    out = out[(out["High"] >= out["Low"]) & (out["Open"] > 0) & (out["High"] > 0) & (out["Low"] > 0) & (out["Close"] > 0)]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """TradingView/Pine-style Wilder RMA: seed with SMA(period), then alpha=1/period."""
    values = pd.to_numeric(series, errors="coerce").to_numpy(float)
    out = np.full(len(values), np.nan, dtype=float)
    if period <= 0 or len(values) < period:
        return pd.Series(out, index=series.index, dtype=float)

    # OHLC cleaning makes TR contiguous and finite. Keep this generic anyway.
    for seed_end in range(period - 1, len(values)):
        seed = values[seed_end - period + 1 : seed_end + 1]
        if np.all(np.isfinite(seed)):
            out[seed_end] = float(np.mean(seed))
            start = seed_end + 1
            break
    else:
        return pd.Series(out, index=series.index, dtype=float)

    alpha = 1.0 / float(period)
    for i in range(start, len(values)):
        v = values[i]
        if not np.isfinite(v):
            # No invented bar / no forward fill. A missing source row should have
            # been dropped; if one survives, restart only after a fresh seed.
            continue
        prev = out[i - 1]
        if np.isfinite(prev):
            out[i] = prev + alpha * (v - prev)
        else:
            window = values[max(0, i - period + 1) : i + 1]
            if len(window) == period and np.all(np.isfinite(window)):
                out[i] = float(np.mean(window))
    return pd.Series(out, index=series.index, dtype=float)


def true_range(df: pd.DataFrame) -> pd.Series:
    ohlc = _ohlc(df)
    if ohlc.empty:
        return pd.Series(dtype=float)
    high, low, close = ohlc["High"], ohlc["Low"], ohlc["Close"]
    prev_close = close.shift(1)
    # pandas max(skipna=True) makes the first TR simply High-Low, matching TV.
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def supertrend(
    df: pd.DataFrame,
    period: int = SUPER_TREND_PERIOD,
    multiplier: float = SUPER_TREND_MULTIPLIER,
) -> pd.DataFrame:
    """SuperTrend exactly following the DTC v13.2 specification.

    * ATR = Wilder RMA, not SMA/EMA.
    * First valid direction is forced to DOWN (-1).
    * Band ratchet uses previous final band and previous close.
    * Direction flips only on CLOSE crossing the previous final band.
    * Inputs are standard/real OHLC. HA is display-only elsewhere.
    """
    ohlc = _ohlc(df)
    columns = [
        "tr", "atr", "hl2", "upper_basic", "lower_basic",
        "upper", "lower", "supertrend", "direction",
    ]
    if ohlc.empty:
        return pd.DataFrame(index=getattr(df, "index", None), columns=columns, dtype=float)

    high = ohlc["High"]
    low = ohlc["Low"]
    close = ohlc["Close"]
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
    ub = upper_basic.to_numpy(float)
    lb = lower_basic.to_numpy(float)

    valid_atr = np.flatnonzero(np.isfinite(a))
    if len(valid_atr):
        first = int(valid_atr[0])
        upper[first] = ub[first]
        lower[first] = lb[first]
        direction[first] = -1.0  # explicit spec initialization
        st[first] = upper[first]

        for i in range(first + 1, n):
            if not all(np.isfinite(v) for v in (ub[i], lb[i], c[i], c[i - 1], upper[i - 1], lower[i - 1])):
                continue

            upper[i] = ub[i] if (ub[i] < upper[i - 1] or c[i - 1] > upper[i - 1]) else upper[i - 1]
            lower[i] = lb[i] if (lb[i] > lower[i - 1] or c[i - 1] < lower[i - 1]) else lower[i - 1]

            # IMPORTANT: compare today's close to yesterday's final bands.
            if c[i] > upper[i - 1]:
                direction[i] = 1.0
            elif c[i] < lower[i - 1]:
                direction[i] = -1.0
            else:
                direction[i] = direction[i - 1]

            st[i] = lower[i] if direction[i] > 0 else upper[i]

    result = pd.DataFrame(
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
    return result



def _grade_from_r(r_pct: float) -> str:
    # Mutually-exclusive if/elif thresholds from the fixed specification.
    if r_pct < 2.0:
        return "BUY_S"
    if r_pct < 5.0:
        return "BUY_A"
    if r_pct < 10.0:
        return "BUY_B"
    if r_pct < 20.0:
        return "BUY_C"
    return "HOLD"


def _opinion_for(
    close: float,
    p0: float,
    p1: float,
    direction: float,
) -> tuple[str, str | None, float | None]:
    """Pure opinion function. P0 MUST already be ST[flip_idx-1]."""
    if not np.isfinite(direction):
        return "HOLD", "NO_FLIP", None
    if direction < 0:
        return "SELL", None, None
    if not (np.isfinite(p0) and p0 > 0 and np.isfinite(p1)):
        return "HOLD", "NO_FLIP", None

    r_pct = (close - p0) / p0 * 100.0 if np.isfinite(close) and close > 0 else np.nan
    if p1 < p0:
        return "HOLD", "BELOW_GATE", float(r_pct) if np.isfinite(r_pct) else None

    # §4.2 invariant. Tiny epsilon protects only floating-point round-off.
    eps = max(1e-12, abs(close) * 1e-12)
    assert close + eps >= p1 >= p0 - eps, f"SuperTrend gate invariant failed: close={close}, p1={p1}, p0={p0}"
    assert np.isfinite(r_pct) and r_pct >= -1e-10, f"r_pct must be non-negative after gate: {r_pct}"
    r_pct = max(0.0, float(r_pct))
    grade = _grade_from_r(r_pct)
    if grade == "HOLD":
        return grade, "OVEREXTENDED", r_pct
    return grade, None, r_pct


def signal_series(
    df: pd.DataFrame,
    period: int = SUPER_TREND_PERIOD,
    multiplier: float = SUPER_TREND_MULTIPLIER,
    warmup_discard: int = WARMUP_DISCARD_BARS,
) -> pd.DataFrame:
    """Causal SuperTrend opinions and leg/gate diagnostics for every bar."""
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
        d = dir_arr[i]
        p1 = st_arr[i]
        close = close_arr[i]
        atr = atr_arr[i]
        prev_d = dir_arr[i - 1] if i > 0 else np.nan
        decision_valid[i] = i >= decision_from and np.isfinite(d) and np.isfinite(p1)

        if np.isfinite(d) and d > 0 and i > 0 and np.isfinite(prev_d) and prev_d < 0:
            current_leg_id += 1
            current_flip = i
            current_gate = None
            current_r_at_gate = np.nan
            # CRITICAL: P0 is the ST value on the bar BEFORE the down->up flip.
            current_p0 = st_arr[i - 1] if np.isfinite(st_arr[i - 1]) else np.nan

        if np.isfinite(d) and d < 0 and i > 0 and np.isfinite(prev_d) and prev_d > 0:
            last_sell_flip = i
            # Rising-leg P0/gate no longer applies in a downtrend.
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

            op, reason, r = _opinion_for(close, current_p0, p1, d)
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


def _cost_rates(market: str, costs: dict[str, float] | None = None) -> tuple[float, float, dict[str, float]]:
    cfg = dict(DEFAULT_COSTS)
    if costs:
        cfg.update({k: float(v) for k, v in costs.items() if v is not None})
    fee = cfg["fee_pct_per_side"] / 100.0
    slip = cfg["slippage_pct_per_side"] / 100.0
    sell_tax = (cfg["kr_sell_tax_pct"] if str(market).upper().startswith("KR") else cfg["us_sell_tax_pct"]) / 100.0
    return fee + slip, fee + slip + sell_tax, cfg


def _net_return(entry: float, exit_: float, buy_cost: float, sell_cost: float) -> float:
    if not (np.isfinite(entry) and entry > 0 and np.isfinite(exit_) and exit_ > 0):
        return np.nan
    paid = entry * (1.0 + buy_cost)
    received = exit_ * (1.0 - sell_cost)
    return (received / paid - 1.0) * 100.0


def _trade_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    returns = np.array([float(t["return_pct"]) for t in trades if np.isfinite(t.get("return_pct", np.nan))], dtype=float)
    holds = np.array([float(t["holding_bars"]) for t in trades if np.isfinite(t.get("holding_bars", np.nan))], dtype=float)
    if not len(returns):
        return {
            "trades": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "median_return_pct": None,
            "avg_gain_pct": None,
            "avg_loss_pct": None,
            "payoff_ratio": None,
            "avg_holding_bars": None,
            "max_gain_pct": None,
            "max_loss_pct": None,
        }
    gains = returns[returns > 0]
    losses = returns[returns < 0]
    avg_gain = float(np.mean(gains)) if len(gains) else None
    avg_loss = float(np.mean(losses)) if len(losses) else None
    payoff = (avg_gain / abs(avg_loss)) if (avg_gain is not None and avg_loss not in (None, 0.0)) else None
    return {
        "trades": int(len(returns)),
        "win_rate_pct": float(np.mean(returns > 0) * 100.0),
        "avg_return_pct": float(np.mean(returns)),
        "median_return_pct": float(np.median(returns)),
        "avg_gain_pct": avg_gain,
        "avg_loss_pct": avg_loss,
        "payoff_ratio": float(payoff) if payoff is not None and np.isfinite(payoff) else None,
        "avg_holding_bars": float(np.mean(holds)) if len(holds) else None,
        "max_gain_pct": float(np.max(returns)),
        "max_loss_pct": float(np.min(returns)),
    }


def _next_sell_flip_positions(direction: np.ndarray) -> np.ndarray:
    n = len(direction)
    next_flip = np.full(n, -1, dtype=int)
    upcoming = -1
    for i in range(n - 1, -1, -1):
        if i + 1 < n and np.isfinite(direction[i]) and np.isfinite(direction[i + 1]) and direction[i] > 0 and direction[i + 1] < 0:
            upcoming = i + 1
        next_flip[i] = upcoming
    return next_flip


def backtest_buy_s_to_sell(
    df: pd.DataFrame,
    market: str = "US",
    sessions: int = BACKTEST_SESSIONS,
    signals: pd.DataFrame | None = None,
    costs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """2Y backtest: first Buy-S per rising leg, next-open entry/exit, 1 leg = 1 trade."""
    signals = signal_series(df) if signals is None else signals
    if signals.empty or len(signals) < MIN_REQUIRED_BARS:
        return {
            "available": False,
            "reason": "insufficient_history_lt_604",
            "completed": _trade_stats([]),
            "including_open": _trade_stats([]),
            "open_trades": 0,
        }

    n = len(signals)
    dates = pd.DatetimeIndex(signals.index)
    last_ts = pd.Timestamp(dates[-1])
    try:
        cutoff = last_ts - pd.DateOffset(years=BACKTEST_YEARS)
        in_window = dates >= cutoff
    except Exception:
        in_window = np.arange(n) >= max(0, n - sessions)
    decision = signals["decision_valid"].fillna(False).to_numpy(bool)
    in_window = np.asarray(in_window, dtype=bool) & decision

    direction = signals["direction"].to_numpy(float)
    opinions = signals["opinion_code"].astype(str).to_numpy()
    opens = signals["Open"].to_numpy(float)
    closes = signals["Close"].to_numpy(float)
    leg_ids = signals["leg_id"].to_numpy(float)
    atr_pcts = signals["atr_pct"].to_numpy(float)
    r_pcts = signals["r_pct"].to_numpy(float)
    hold_reasons = signals["hold_reason"].astype(object).to_numpy()
    gate_pos = signals["gate_pos"].to_numpy(float)

    buy_cost, sell_cost, cfg = _cost_rates(market, costs)
    completed: list[dict[str, Any]] = []
    open_marked: list[dict[str, Any]] = []
    entered_legs: set[int] = set()
    holding: dict[str, Any] | None = None

    for i in range(n):
        if holding is not None and i > 0 and np.isfinite(direction[i]) and np.isfinite(direction[i - 1]) and direction[i - 1] > 0 and direction[i] < 0:
            # Signal known at close i; execute at next session open.
            if i + 1 < n and np.isfinite(opens[i + 1]) and opens[i + 1] > 0:
                exit_price = float(opens[i + 1])
                ret = _net_return(holding["entry_price"], exit_price, buy_cost, sell_cost)
                completed.append({
                    **holding,
                    "sell_signal_date": dates[i].date().isoformat(),
                    "sell_date": dates[i + 1].date().isoformat(),
                    "sell_price": exit_price,
                    "return_pct": float(ret),
                    "gross_return_pct": float((exit_price / holding["entry_price"] - 1.0) * 100.0),
                    "holding_bars": int((i + 1) - holding["entry_pos"]),
                    "closed": True,
                })
                holding = None
            # If there is no next bar, leave it open and mark to last close below.

        if holding is None and in_window[i] and opinions[i] == "BUY_S" and np.isfinite(leg_ids[i]):
            leg = int(leg_ids[i])
            if leg in entered_legs:
                continue
            if i + 1 >= n or not (np.isfinite(opens[i + 1]) and opens[i + 1] > 0):
                continue
            entered_legs.add(leg)
            holding = {
                "leg_id": leg,
                "buy_signal_date": dates[i].date().isoformat(),
                "buy_date": dates[i + 1].date().isoformat(),
                "entry_price": float(opens[i + 1]),
                "entry_pos": int(i + 1),
                "entry_r_pct": float(r_pcts[i]) if np.isfinite(r_pcts[i]) else None,
                "entry_atr_pct": float(atr_pcts[i]) if np.isfinite(atr_pcts[i]) else None,
            }

    if holding is not None and np.isfinite(closes[-1]) and closes[-1] > 0:
        mark_price = float(closes[-1])
        ret = _net_return(holding["entry_price"], mark_price, buy_cost, sell_cost)
        open_marked.append({
            **holding,
            "mark_date": dates[-1].date().isoformat(),
            "mark_price": mark_price,
            "return_pct": float(ret),
            "gross_return_pct": float((mark_price / holding["entry_price"] - 1.0) * 100.0),
            "holding_bars": int((n - 1) - holding["entry_pos"]),
            "closed": False,
        })

    all_marked = completed + open_marked

    # Same-stock passive benchmark over the same two-year calendar window.
    eligible_pos = np.flatnonzero(in_window)
    buy_hold_return = None
    if len(eligible_pos):
        start_signal = int(eligible_pos[0])
        entry_pos = min(start_signal + 1, n - 1)
        if np.isfinite(opens[entry_pos]) and opens[entry_pos] > 0 and np.isfinite(closes[-1]) and closes[-1] > 0:
            buy_hold_return = _net_return(float(opens[entry_pos]), float(closes[-1]), buy_cost, sell_cost)

    # Grade-validation events: every post-gate bar + first occurrence of each
    # grade within each leg, measured from adjusted close and without trade costs.
    next_sell = _next_sell_flip_positions(direction)
    grade_samples: list[dict[str, Any]] = []
    first_grade_samples: list[dict[str, Any]] = []
    seen_leg_grade: set[tuple[int, str]] = set()
    gate_events: list[dict[str, Any]] = []
    seen_gate_legs: set[int] = set()

    for i in range(n):
        if not in_window[i] or not np.isfinite(leg_ids[i]) or not np.isfinite(gate_pos[i]):
            continue
        leg = int(leg_ids[i])
        gate_i = int(gate_pos[i])
        if i < gate_i:
            continue

        if leg not in seen_gate_legs and i == gate_i:
            seen_gate_legs.add(leg)
            gate_events.append({
                "leg_id": leg,
                "date": dates[i].date().isoformat(),
                "r_at_gate": float(r_pcts[i]) if np.isfinite(r_pcts[i]) else None,
                "atr_pct": float(atr_pcts[i]) if np.isfinite(atr_pcts[i]) else None,
            })

        op = opinions[i]
        if op in {"BUY_S", "BUY_A", "BUY_B", "BUY_C"}:
            grade = op
        elif op == "HOLD" and hold_reasons[i] == "OVEREXTENDED":
            grade = "HOLD_OVEREXTENDED"
        else:
            continue

        if not np.isfinite(closes[i]) or closes[i] <= 0:
            continue
        sample: dict[str, Any] = {
            "leg_id": leg,
            "date": dates[i].date().isoformat(),
            "grade": grade,
            "r_pct": float(r_pcts[i]) if np.isfinite(r_pcts[i]) else None,
            "atr_pct": float(atr_pcts[i]) if np.isfinite(atr_pcts[i]) else None,
        }
        for h in (5, 20, 60):
            j = i + h
            sample[f"fwd_{h}d_pct"] = float((closes[j] / closes[i] - 1.0) * 100.0) if j < n and np.isfinite(closes[j]) and closes[j] > 0 else None
        sell_i = int(next_sell[i]) if next_sell[i] >= 0 else -1
        sample["to_sell_pct"] = float((closes[sell_i] / closes[i] - 1.0) * 100.0) if sell_i > i and np.isfinite(closes[sell_i]) and closes[sell_i] > 0 else None
        grade_samples.append(sample)
        key = (leg, grade)
        if key not in seen_leg_grade:
            seen_leg_grade.add(key)
            first_grade_samples.append(dict(sample))

    daily = []
    valid_positions = np.flatnonzero(decision)
    for i in valid_positions[-60:]:
        daily.append({"date": dates[i].date().isoformat(), "opinion_code": str(opinions[i])})

    return {
        "available": True,
        "window": "last 2 calendar years",
        "period_start": dates[np.flatnonzero(in_window)[0]].date().isoformat() if np.any(in_window) else None,
        "period_end": dates[-1].date().isoformat(),
        "entry_rule": "first Buy S per rising leg; execute next bar open",
        "exit_rule": "UP->DOWN flip; execute next bar open",
        "completed": _trade_stats(completed),
        "including_open": _trade_stats(all_marked),
        "open_trades": int(len(open_marked)),
        "open_mark_return_pct": float(open_marked[0]["return_pct"]) if open_marked else None,
        "buy_hold_return_pct": float(buy_hold_return) if buy_hold_return is not None and np.isfinite(buy_hold_return) else None,
        "costs": {
            **cfg,
            "sell_tax_pct_applied": cfg["kr_sell_tax_pct"] if str(market).upper().startswith("KR") else cfg["us_sell_tax_pct"],
        },
        "recent_trades": completed[-10:],
        "_research": {
            "trades": completed,
            "open_trades": open_marked,
            "grade_samples": grade_samples,
            "first_grade_samples": first_grade_samples,
            "gate_events": gate_events,
            "daily_opinions": daily,
        },
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
    """Current opinion + 2Y backtest + 6M real-OHLC/SuperTrend chart payload."""
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

    backtest = backtest_buy_s_to_sell(ohlc, market=market, signals=signals, costs=costs)
    research = dict(backtest.pop("_research", {}) or {})

    chart_start = max(0, len(signals) - CHART_SESSIONS)
    chart = []
    current_flip = int(row["flip_pos"]) if np.isfinite(row.get("flip_pos", np.nan)) else None
    current_gate = int(row["gate_pos"]) if np.isfinite(row.get("gate_pos", np.nan)) else None
    current_p0 = p0 if direction is not None and direction > 0 else None

    # Chart candles are the same adjusted real OHLC used by the strategy.
    # This intentionally removes the former Heikin-Ashi display layer so the
    # candle and SuperTrend price levels are directly comparable.
    for pos in range(chart_start, len(signals)):
        idx = signals.index[pos]
        sr = signals.iloc[pos]
        o = _safe_float(sr.get("Open"))
        h = _safe_float(sr.get("High"))
        l = _safe_float(sr.get("Low"))
        c = _safe_float(sr.get("Close"))
        if any(v is None for v in (o, h, l, c)):
            continue
        stv = _safe_float(sr.get("supertrend"))
        d = int(sr["direction"]) if np.isfinite(sr.get("direction", np.nan)) else None
        chart.append({
            "date": pd.Timestamp(idx).date().isoformat(),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "supertrend": stv,
            "direction": d,
            "p0_line": float(current_p0) if current_p0 is not None and current_flip is not None and pos >= current_flip else None,
            "is_flip": bool(current_flip is not None and pos == current_flip),
            "is_gate": bool(current_gate is not None and pos == current_gate),
        })

    bars_since_sell_flip = None
    if opinion_code == "SELL" and np.isfinite(row.get("sell_flip_pos", np.nan)):
        bars_since_sell_flip = int(current_pos - int(row["sell_flip_pos"]))

    result = {
        "available": bool(p1 is not None and direction is not None),
        "period": int(period),
        "multiplier": float(multiplier),
        "atr_smoothing": "Wilder RMA",
        "warmup_discard_bars": int(WARMUP_DISCARD_BARS),
        "direction": "UP" if direction is not None and direction > 0 else "DOWN" if direction is not None else None,
        "opinion": opinion_label,
        "opinion_code": opinion_code,
        "opinion_label": opinion_label,
        "hold_reason": hold_reason,
        "rank_level": int(OPINION_ORDER.get(opinion_code, OPINION_ORDER["HOLD"])),
        "p0": p0,
        "p1": p1,
        "current_close": close,
        "r_pct": _safe_float(row.get("r_pct")),
        "stop_pct": _safe_float(row.get("stop_pct")),
        "atr_pct": _safe_float(row.get("atr_pct")),
        "g_atr": _safe_float(row.get("g_atr")),
        "d_atr": _safe_float(row.get("d_atr")),
        "bars_since_flip": int(row["bars_since_flip"]) if np.isfinite(row.get("bars_since_flip", np.nan)) else None,
        "bars_since_gate": int(row["bars_since_gate"]) if np.isfinite(row.get("bars_since_gate", np.nan)) else None,
        "r_at_gate": _safe_float(row.get("r_at_gate")),
        "new_sell": new_sell,
        "bars_since_sell_flip": bars_since_sell_flip,
        "flip_date": pd.Timestamp(signals.index[int(row["flip_pos"])]).date().isoformat() if np.isfinite(row.get("flip_pos", np.nan)) else None,
        "gate_date": pd.Timestamp(signals.index[int(row["gate_pos"])]).date().isoformat() if np.isfinite(row.get("gate_pos", np.nan)) else None,
        "backtest": backtest,
        "chart": chart,
        "_research": research,
    }
    return result
