import unittest
import numpy as np
import pandas as pd

from supertrend_strategy import (
    SUPER_TREND_PERIOD,
    SUPER_TREND_MULTIPLIER,
    adx,
    supertrend,
    add_up_flip_reference,
    classify_supertrend,
    signal_series,
    compute_buy_cycles,
    analyze,
    _current_case1_age,
)


def frame_from_close(values, wick=0.012):
    idx = pd.bdate_range("2022-01-03", periods=len(values))
    c = np.asarray(values, dtype=float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1.0 + wick)
    l = np.minimum(o, c) * (1.0 - wick)
    return pd.DataFrame(
        {"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1_000_000.0},
        index=idx,
    )


class SingleSupertrend204Tests(unittest.TestCase):
    def test_parameters_are_20_4(self):
        self.assertEqual(SUPER_TREND_PERIOD, 20)
        self.assertEqual(SUPER_TREND_MULTIPLIER, 4.0)

    def test_tradingview_daily_st_and_adx_warmup(self):
        c = np.linspace(100, 160, 180) + 3 * np.sin(np.arange(180) / 6)
        df = frame_from_close(c)
        st = supertrend(df, 20, 4.0)
        ax = adx(df, 14, 14)
        self.assertEqual(int(np.flatnonzero(np.isfinite(st["ATR"].to_numpy()))[0]), 19)
        self.assertEqual(int(np.flatnonzero(np.isfinite(ax["ADX"].to_numpy()))[0]), 27)

    def test_previous_down_reference_uses_last_down_st(self):
        idx = pd.bdate_range("2025-01-01", periods=5)
        d = pd.DataFrame(
            {"ST_DIR": [-1, -1, 1, 1, 1], "ST": [110, 108, 95, 108, 109]},
            index=idx,
        )
        out = add_up_flip_reference(d)
        self.assertEqual(float(out["ST_UP_FLIP_REF"].iloc[2]), 108.0)
        self.assertEqual(float(out["ST_UP_FLIP_AGE"].iloc[2]), 0.0)
        self.assertEqual(float(out["ST_UP_FLIP_AGE"].iloc[3]), 1.0)

    def test_buy_requires_uptrend_and_breakout(self):
        self.assertEqual(classify_supertrend(1, 110, 100, 0)[0], "BUY")
        self.assertEqual(classify_supertrend(1, 100, 100, 0)[0], "BUY")
        self.assertEqual(classify_supertrend(1, 99.99, 100, 5)[0], "SELL")
        self.assertEqual(classify_supertrend(-1, 120, 100, 5)[0], "SELL")

    def test_flip_bar_can_be_buy_plus_zero(self):
        idx = pd.to_datetime(["2026-08-24", "2026-08-25", "2026-08-26"])
        d = pd.DataFrame({"CASE1": [False, False, True]}, index=idx)
        signal_date, age_days, age_sessions = _current_case1_age(d)
        self.assertEqual(signal_date, "2026-08-26")
        self.assertEqual(age_days, 0)
        self.assertEqual(age_sessions, 0)

    def test_buy_age_uses_first_continuous_breakout_day(self):
        idx = pd.to_datetime(["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"])
        d = pd.DataFrame({"CASE1": [False, True, True, True]}, index=idx)
        signal_date, age_days, age_sessions = _current_case1_age(d)
        self.assertEqual(signal_date, "2026-08-21")
        self.assertEqual(age_days, 4)
        self.assertEqual(age_sessions, 2)

    def test_backtest_buy_to_first_sell(self):
        idx = pd.bdate_range("2025-01-01", periods=7)
        d = pd.DataFrame(
            {
                "Open": [100, 101, 102, 103, 104, 105, 106],
                "High": [101, 103, 108, 112, 115, 111, 109],
                "Low": [99, 100, 101, 102, 103, 104, 105],
                "Close": [100, 102, 105, 108, 110, 109, 108],
                "opinion_code": ["SELL", "BUY", "BUY", "BUY", "BUY", "SELL", "SELL"],
            },
            index=idx,
        )
        out = compute_buy_cycles(d, years=2)
        self.assertEqual(out["completed_events"], 1)
        self.assertEqual(out["events"][0]["time"], idx[1].strftime("%Y-%m-%d"))
        self.assertEqual(out["events"][0]["exit_time"], idx[5].strftime("%Y-%m-%d"))
        self.assertAlmostEqual(out["events"][0]["max_return_pct"], (115 / 102 - 1) * 100, places=10)

    def test_signal_and_analyze_have_no_weekly_supertrend(self):
        n = 760
        x = np.arange(n, dtype=float)
        df = frame_from_close(100 + 0.05 * x + 10 * np.sin(x / 19.0))
        sig = signal_series(df)
        for col in ("ADX", "ST", "ST_DIR", "ST_UP_FLIP_REF", "CASE1", "opinion_code"):
            self.assertIn(col, sig.columns)
        self.assertNotIn("W_ST", sig.columns)
        self.assertNotIn("CASE2", sig.columns)
        self.assertIn(str(sig.iloc[-1].opinion_code), {"BUY", "SELL"})

        result = analyze(df)
        self.assertTrue(result["available"])
        self.assertEqual(result["period"], 20)
        self.assertEqual(result["multiplier"], 4.0)
        self.assertEqual(len(result["chart"]), 126)
        self.assertNotIn("weekly_supertrend", result["chart"][-1])
        self.assertNotIn("st_w_direction", result)
        self.assertNotIn("case2", result)
        self.assertIn("st_reference_value", result)
        if result["opinion_code"] == "BUY":
            self.assertRegex(result["opinion_label"], r"^BUY \(\+\d+\)$")


if __name__ == "__main__":
    unittest.main()
