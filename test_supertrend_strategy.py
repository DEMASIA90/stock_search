import unittest

import numpy as np
import pandas as pd

from supertrend_strategy import (
    _opinion_for,
    analyze,
    backtest_strong_buy_stats,
    reference_setups,
    signal_series,
    supertrend,
    wilder_rma,
)


def frame_from_close(values, wick=0.01, volume=None):
    idx = pd.bdate_range("2023-01-02", periods=len(values))
    c = np.asarray(values, dtype=float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1.0 + wick)
    l = np.minimum(o, c) * (1.0 - wick)
    vol = np.full(len(c), 1_000_000.0) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": vol}, index=idx)


def tradingview_reference(df, period=10, multiplier=3.0):
    """Direct translation of TradingView's documented pine_supertrend()."""
    high = df["High"].to_numpy(float)
    low = df["Low"].to_numpy(float)
    close = df["Close"].to_numpy(float)
    n = len(df)
    tr = np.full(n, np.nan)
    for i in range(n):
        tr[i] = high[i] - low[i] if i == 0 else max(
            high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])
        )
    atr = np.full(n, np.nan)
    if n >= period:
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.full(n, np.nan)  # DTC sign: +1 UP, -1 DOWN
    for i in range(n):
        if not np.isfinite(atr[i]):
            continue
        ub = (high[i] + low[i]) / 2 + multiplier * atr[i]
        lb = (high[i] + low[i]) / 2 - multiplier * atr[i]
        if i > 0 and np.isfinite(upper[i - 1]):
            prev_u, prev_l = upper[i - 1], lower[i - 1]
            lb = lb if (lb > prev_l or close[i - 1] < prev_l) else prev_l
            ub = ub if (ub < prev_u or close[i - 1] > prev_u) else prev_u
        upper[i], lower[i] = ub, lb
        if i == 0 or not np.isfinite(atr[i - 1]) or not np.isfinite(st[i - 1]):
            direction[i] = -1
        elif np.isclose(st[i - 1], upper[i - 1], rtol=1e-12, atol=1e-12):
            direction[i] = 1 if close[i] > upper[i] else -1
        else:
            direction[i] = -1 if close[i] < lower[i] else 1
        st[i] = lower[i] if direction[i] > 0 else upper[i]
    return atr, upper, lower, direction, st


class SupertrendTests(unittest.TestCase):
    def test_wilder_rma(self):
        s = pd.Series(np.arange(1.0, 31.0))
        out = wilder_rma(s, 10)
        self.assertAlmostEqual(out.iloc[9], 5.5, places=12)
        self.assertAlmostEqual(out.iloc[10], (5.5 * 9 + 11) / 10, places=12)

    def test_matches_tradingview_reference(self):
        c = 100 + 8 * np.sin(np.linspace(0, 9 * np.pi, 300)) + np.linspace(0, 20, 300)
        df = frame_from_close(c, wick=0.018)
        got = supertrend(df)
        atr, ub, lb, direction, st = tradingview_reference(df)
        np.testing.assert_allclose(got["atr"], atr, equal_nan=True, atol=1e-11, rtol=0)
        np.testing.assert_allclose(got["upper"], ub, equal_nan=True, atol=1e-11, rtol=0)
        np.testing.assert_allclose(got["lower"], lb, equal_nan=True, atol=1e-11, rtol=0)
        np.testing.assert_allclose(got["direction"], direction, equal_nan=True, atol=0, rtol=0)
        np.testing.assert_allclose(got["supertrend"], st, equal_nan=True, atol=1e-11, rtol=0)

    def test_p0_previous_bar_st(self):
        c = np.r_[np.linspace(170, 95, 140), np.linspace(95, 175, 160)]
        df = frame_from_close(c)
        st = supertrend(df)
        d = st["direction"].to_numpy(float)
        flips = [i for i in range(1, len(d)) if np.isfinite(d[i - 1]) and d[i - 1] < 0 and d[i] > 0]
        self.assertTrue(flips)
        flip = flips[-1]
        sig = signal_series(df, warmup_discard=0)
        self.assertAlmostEqual(float(sig.iloc[flip]["p0"]), float(st.iloc[flip - 1]["supertrend"]), places=10)

    def test_gate_invariant_and_latch(self):
        c = 110 + 14 * np.sin(np.linspace(0, 20 * np.pi, 850)) + np.linspace(0, 35, 850)
        sig = signal_series(frame_from_close(c), warmup_discard=0)
        checked = 0
        for leg_id, leg in sig[(sig["direction"] > 0) & sig["leg_id"].notna()].groupby("leg_id"):
            gate = leg["gate_pos"].notna().to_numpy()
            if not gate.any():
                continue
            first = int(np.argmax(gate))
            self.assertTrue(gate[first:].all())
            gated = leg.iloc[first:]
            self.assertTrue(np.all(gated["Close"].to_numpy() + 1e-9 >= gated["supertrend"].to_numpy()))
            self.assertTrue(np.all(gated["supertrend"].to_numpy() + 1e-9 >= gated["p0"].to_numpy()))
            checked += 1
        self.assertGreater(checked, 0)

    def test_strong_buy_gate_window(self):
        self.assertEqual(_opinion_for(105, 100, 101, 1, 0)[0], "STRONG_BUY")
        self.assertEqual(_opinion_for(105, 100, 101, 1, 3)[0], "STRONG_BUY")
        self.assertEqual(_opinion_for(105, 100, 101, 1, 4)[0], "BUY")
        self.assertEqual(_opinion_for(101, 100, 99, 1, None)[:2], ("HOLD", "BELOW_GATE"))
        self.assertEqual(_opinion_for(101, 100, 99, -1, None)[0], "SELL")

    def test_chart_uses_real_ohlc(self):
        c = 100 + 11 * np.sin(np.linspace(0, 15 * np.pi, 700)) + np.linspace(0, 22, 700)
        df = frame_from_close(c)
        result = analyze(df)
        self.assertTrue(result["available"])
        self.assertEqual(len(result["chart"]), 126)
        tail = df.tail(126)
        self.assertAlmostEqual(result["chart"][-1]["open"], float(tail["Open"].iloc[-1]), places=10)
        self.assertAlmostEqual(result["chart"][-1]["close"], float(tail["Close"].iloc[-1]), places=10)

    def test_reference_setups_are_reference_only(self):
        c = np.linspace(100, 140, 100)
        vol = np.full(100, 1_000_000.0)
        vol[-1] = 2_000_000.0
        refs = reference_setups(frame_from_close(c, volume=vol), 1.0)
        self.assertTrue(refs["reference_only"])
        self.assertIn(refs["breakout"]["label"], {"좋음", "보통", "나쁨"})
        self.assertIn(refs["pullback"]["label"], {"좋음", "보통", "나쁨"})

    def test_backtest_summary_is_deterministic(self):
        c = 100 + 15 * np.sin(np.linspace(0, 24 * np.pi, 800)) + np.linspace(0, 30, 800)
        df = frame_from_close(c, wick=0.025)
        sig = signal_series(df)
        a = backtest_strong_buy_stats(df, signals=sig)
        b = backtest_strong_buy_stats(df, signals=sig)
        self.assertEqual(a, b)
        if a["win_rate_20d_pct"] is not None:
            self.assertGreaterEqual(a["win_rate_20d_pct"], 0)
            self.assertLessEqual(a["win_rate_20d_pct"], 100)
        if a["max_return_median_pct"] is not None:
            self.assertTrue(np.isfinite(a["max_return_median_pct"]))

    def test_backtest_metrics_exact_fixture(self):
        n = 620
        idx = pd.bdate_range("2023-01-02", periods=n)
        base = pd.DataFrame(index=idx)
        base["Open"] = 100.0
        base["High"] = 100.0
        base["Low"] = 99.0
        base["Close"] = 100.0
        base["Volume"] = 1_000_000.0

        sig = base.copy()
        sig["decision_valid"] = True
        sig["direction"] = -1.0
        sig["leg_id"] = np.nan
        sig["opinion_code"] = "SELL"

        # One rising leg: Strong Buy at 150, entry at 151 open=100, Sell at 180.
        sig.iloc[140:180, sig.columns.get_loc("direction")] = 1.0
        sig.iloc[140:180, sig.columns.get_loc("leg_id")] = 1.0
        sig.iloc[140:180, sig.columns.get_loc("opinion_code")] = "BUY"
        sig.iloc[150, sig.columns.get_loc("opinion_code")] = "STRONG_BUY"
        sig.iloc[151:181, sig.columns.get_loc("High")] = 105.0
        sig.iloc[160, sig.columns.get_loc("High")] = 120.0
        sig.iloc[171, sig.columns.get_loc("Close")] = 101.0

        bt = backtest_strong_buy_stats(base, signals=sig)
        self.assertAlmostEqual(bt["max_return_median_pct"], 20.0, places=10)
        self.assertAlmostEqual(bt["win_rate_20d_pct"], 100.0, places=10)
        self.assertEqual(bt["max_return_samples"], 1)
        self.assertEqual(bt["win_20d_samples"], 1)


if __name__ == "__main__":
    unittest.main()
