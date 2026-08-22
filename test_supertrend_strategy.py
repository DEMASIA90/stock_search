import unittest

import numpy as np
import pandas as pd

from supertrend_strategy import (
    _opinion_for,
    analyze,
    backtest_buy_s_to_sell,
    signal_series,
    supertrend,
    true_range,
    wilder_rma,
)


def frame_from_close(values, wick=0.01):
    idx = pd.bdate_range("2023-01-02", periods=len(values))
    c = np.asarray(values, dtype=float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1.0 + wick)
    l = np.minimum(o, c) * (1.0 - wick)
    return pd.DataFrame(
        {"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1_000_000},
        index=idx,
    )


def reference_supertrend(df, period=10, multiplier=2.0):
    """Independent loop implementation of the fixed §2 equations."""
    high = df["High"].astype(float).to_numpy()
    low = df["Low"].astype(float).to_numpy()
    close = df["Close"].astype(float).to_numpy()
    n = len(df)
    tr = np.full(n, np.nan)
    for i in range(n):
        if i == 0:
            tr[i] = high[i] - low[i]
        else:
            tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = np.full(n, np.nan)
    if n >= period:
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    hl2 = (high + low) / 2.0
    ub_basic = hl2 + multiplier * atr
    lb_basic = hl2 - multiplier * atr
    ub = np.full(n, np.nan)
    lb = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    st = np.full(n, np.nan)
    if n >= period:
        first = period - 1
        ub[first] = ub_basic[first]
        lb[first] = lb_basic[first]
        direction[first] = -1
        st[first] = ub[first]
        for i in range(first + 1, n):
            ub[i] = ub_basic[i] if (ub_basic[i] < ub[i - 1] or close[i - 1] > ub[i - 1]) else ub[i - 1]
            lb[i] = lb_basic[i] if (lb_basic[i] > lb[i - 1] or close[i - 1] < lb[i - 1]) else lb[i - 1]
            if close[i] > ub[i - 1]:
                direction[i] = 1
            elif close[i] < lb[i - 1]:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]
            st[i] = lb[i] if direction[i] > 0 else ub[i]
    return atr, ub, lb, direction, st


class SupertrendTests(unittest.TestCase):
    def test_wilder_rma_seed_and_recurrence(self):
        s = pd.Series(np.arange(1.0, 31.0))
        out = wilder_rma(s, 10)
        self.assertAlmostEqual(out.iloc[9], np.mean(np.arange(1.0, 11.0)), places=12)
        expected = (out.iloc[9] * 9.0 + 11.0) / 10.0
        self.assertAlmostEqual(out.iloc[10], expected, places=12)

    def test_supertrend_matches_independent_spec_reference(self):
        c = 100 + 8 * np.sin(np.linspace(0, 8 * np.pi, 240)) + np.linspace(0, 25, 240)
        df = frame_from_close(c, wick=0.018)
        got = supertrend(df)
        atr, ub, lb, direction, st = reference_supertrend(df)
        np.testing.assert_allclose(got["atr"].to_numpy(), atr, equal_nan=True, rtol=0, atol=1e-11)
        np.testing.assert_allclose(got["upper"].to_numpy(), ub, equal_nan=True, rtol=0, atol=1e-11)
        np.testing.assert_allclose(got["lower"].to_numpy(), lb, equal_nan=True, rtol=0, atol=1e-11)
        np.testing.assert_allclose(got["direction"].to_numpy(), direction, equal_nan=True, rtol=0, atol=0)
        np.testing.assert_allclose(got["supertrend"].to_numpy(), st, equal_nan=True, rtol=0, atol=1e-11)

    def test_p0_is_previous_bar_st_not_flip_bar_st(self):
        c = np.r_[np.linspace(170, 95, 140), np.linspace(95, 175, 160)]
        df = frame_from_close(c)
        st = supertrend(df)
        d = st["direction"].to_numpy(float)
        flips = [i for i in range(1, len(d)) if np.isfinite(d[i - 1]) and d[i - 1] < 0 and d[i] > 0]
        self.assertTrue(flips)
        flip = flips[-1]
        sig = signal_series(df, warmup_discard=0)
        row = sig.iloc[flip]
        self.assertAlmostEqual(float(row["p0"]), float(st["supertrend"].iloc[flip - 1]), places=10)
        self.assertGreater(float(st["supertrend"].iloc[flip - 1]), float(st["supertrend"].iloc[flip]))

    def test_gate_invariant_close_above_p1_above_p0(self):
        c = 110 + 15 * np.sin(np.linspace(0, 18 * np.pi, 800)) + np.linspace(0, 35, 800)
        sig = signal_series(frame_from_close(c))
        gated = sig[(sig["decision_valid"]) & (sig["direction"] > 0) & sig["gate_pos"].notna()]
        self.assertGreater(len(gated), 0)
        self.assertTrue(np.all(gated["Close"].to_numpy() + 1e-9 >= gated["supertrend"].to_numpy()))
        self.assertTrue(np.all(gated["supertrend"].to_numpy() + 1e-9 >= gated["p0"].to_numpy()))
        self.assertTrue(np.all(gated["r_pct"].to_numpy() >= -1e-9))

    def test_gate_is_latched_within_each_rising_leg(self):
        c = 110 + 13 * np.sin(np.linspace(0, 20 * np.pi, 850)) + np.linspace(0, 30, 850)
        sig = signal_series(frame_from_close(c), warmup_discard=0)
        checked = 0
        for leg_id, leg in sig[(sig["direction"] > 0) & sig["leg_id"].notna()].groupby("leg_id"):
            gate_mask = leg["gate_pos"].notna().to_numpy()
            if not gate_mask.any():
                continue
            first = int(np.argmax(gate_mask))
            self.assertTrue(gate_mask[first:].all(), f"gate unlatch in leg {leg_id}")
            gated = leg.iloc[first:]
            self.assertTrue(np.all(gated["supertrend"].to_numpy() + 1e-9 >= gated["p0"].to_numpy()))
            checked += 1
        self.assertGreater(checked, 0)

    def test_grade_thresholds_are_exclusive(self):
        self.assertEqual(_opinion_for(101.0, 100.0, 100.5, 1.0)[0], "BUY_S")
        self.assertEqual(_opinion_for(103.0, 100.0, 100.5, 1.0)[0], "BUY_A")
        self.assertEqual(_opinion_for(107.0, 100.0, 100.5, 1.0)[0], "BUY_B")
        self.assertEqual(_opinion_for(115.0, 100.0, 100.5, 1.0)[0], "BUY_C")
        self.assertEqual(_opinion_for(125.0, 100.0, 100.5, 1.0)[0], "HOLD")

    def test_sell_overrides_and_below_gate_holds(self):
        self.assertEqual(_opinion_for(101.0, 100.0, 99.0, -1.0)[0], "SELL")
        self.assertEqual(_opinion_for(101.0, 100.0, 99.5, 1.0)[:2], ("HOLD", "BELOW_GATE"))

    def test_chart_uses_real_ohlc_not_heikin_ashi(self):
        c = 100 + 11 * np.sin(np.linspace(0, 15 * np.pi, 700)) + np.linspace(0, 22, 700)
        df = frame_from_close(c)
        result = analyze(df)
        self.assertTrue(result["available"])
        chart = result["chart"]
        self.assertEqual(len(chart), 126)
        tail = df.tail(126)
        self.assertAlmostEqual(chart[-1]["open"], float(tail["Open"].iloc[-1]), places=10)
        self.assertAlmostEqual(chart[-1]["high"], float(tail["High"].iloc[-1]), places=10)
        self.assertAlmostEqual(chart[-1]["low"], float(tail["Low"].iloc[-1]), places=10)
        self.assertAlmostEqual(chart[-1]["close"], float(tail["Close"].iloc[-1]), places=10)

    def test_backtest_is_deterministic_and_daily_diagnostics_capped_60(self):
        c = 100 + 12 * np.sin(np.linspace(0, 22 * np.pi, 760)) + np.linspace(0, 24, 760)
        df = frame_from_close(c)
        signals = signal_series(df)
        a = backtest_buy_s_to_sell(df, signals=signals)
        b = backtest_buy_s_to_sell(df, signals=signals)
        self.assertEqual(a, b)
        self.assertLessEqual(len(a.get("_research", {}).get("daily_opinions", [])), 60)


if __name__ == "__main__":
    unittest.main()
