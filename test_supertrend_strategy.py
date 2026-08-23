import unittest

import numpy as np
import pandas as pd

from supertrend_strategy import (
    SUPER_TREND_PERIOD,
    SUPER_TREND_MULTIPLIER,
    add_up_flip_reference,
    adx,
    analyze,
    classify_supertrad_index,
    compute_buy_cycles,
    signal_series,
    supertrend,
)


def frame_from_close(values, wick=0.01):
    idx = pd.bdate_range("2023-01-02", periods=len(values))
    c = np.asarray(values, dtype=float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1.0 + wick)
    l = np.minimum(o, c) * (1.0 - wick)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1_000_000.0}, index=idx)


class SupertradIndexTests(unittest.TestCase):
    def test_parameters_are_14_2(self):
        self.assertEqual(SUPER_TREND_PERIOD, 14)
        self.assertEqual(SUPER_TREND_MULTIPLIER, 2.0)

    def test_supertrend_runs(self):
        n = 300
        x = np.arange(n, dtype=float)
        close = 100 + np.sin(x / 9) * 3 + x * 0.06
        out = supertrend(frame_from_close(close), 14, 2.0)
        self.assertTrue(np.isfinite(out["ST"].iloc[-1]))
        self.assertIn(int(out["ST_DIR"].iloc[-1]), (-1, 1))

    def test_adx14_14_is_finite(self):
        close = np.linspace(80, 150, 300) + np.sin(np.arange(300) / 4.0)
        out = adx(frame_from_close(close), 14, 14)
        self.assertTrue(np.isfinite(out["ADX"].iloc[-1]))
        self.assertGreaterEqual(float(out["ADX"].iloc[-1]), 0.0)

    def test_decision_table_exact(self):
        self.assertEqual(classify_supertrad_index(1, 19.99, 105, 100, 1)[0], "HOLD")
        self.assertEqual(classify_supertrad_index(1, 20.0, 105, 100, 1)[0], "STRONG_BUY")
        self.assertEqual(classify_supertrad_index(1, 24.999, 105, 100, 1)[0], "STRONG_BUY")
        self.assertEqual(classify_supertrad_index(1, 20.0, 100, 100, 1)[0], "STRONG_BUY")
        self.assertEqual(classify_supertrad_index(1, 20.0, 99, 100, 1)[0], "HOLD")
        self.assertEqual(classify_supertrad_index(1, 20.0, 105, 100, 0)[0], "HOLD")
        self.assertEqual(classify_supertrad_index(1, 25.0, 99, 100)[0], "BUY")
        self.assertEqual(classify_supertrad_index(1, 29.999, 99, 100)[0], "BUY")
        self.assertEqual(classify_supertrad_index(1, 30.0, 99, 100)[0], "HOLD")
        self.assertEqual(classify_supertrad_index(1, 40.0, 99, 100)[0], "SELL")
        self.assertEqual(classify_supertrad_index(-1, 69.999, 120, None)[0], "SELL")
        self.assertEqual(classify_supertrad_index(-1, 70.0, 120, None)[0], "STRONG_SELL")
        self.assertEqual(classify_supertrad_index(-1, 22.0, 100, None)[0], "HOLD")
        self.assertEqual(classify_supertrad_index(-1, 35.0, 100, None)[0], "HOLD")

    def test_flip_reference_is_previous_down_st_and_flip_bar_excluded(self):
        idx = pd.bdate_range("2025-01-01", periods=6)
        d = pd.DataFrame({"ST_DIR": [-1, -1, 1, 1, -1, 1], "ST": [110, 108, 95, 108, 112, 100]}, index=idx)
        out = add_up_flip_reference(d)
        self.assertTrue(np.isnan(out["ST_UP_FLIP_REF"].iloc[1]))
        self.assertEqual(float(out["ST_UP_FLIP_REF"].iloc[2]), 108.0)
        self.assertEqual(float(out["ST_UP_FLIP_AGE"].iloc[2]), 0.0)
        self.assertEqual(float(out["ST_UP_FLIP_REF"].iloc[3]), 108.0)
        self.assertEqual(float(out["ST_UP_FLIP_AGE"].iloc[3]), 1.0)
        self.assertEqual(float(out["ST_UP_FLIP_REF"].iloc[5]), 112.0)
        self.assertEqual(float(out["ST_UP_FLIP_AGE"].iloc[5]), 0.0)

    def test_cycle_fixture_matches_local_v1142(self):
        idx = pd.bdate_range("2025-01-01", periods=13)
        d = pd.DataFrame({
            "Open": [100]*13,
            "High": [101,101,102,108,112,120,111,101,108,112,106,105,104],
            "Low": [99]*13,
            "Close": [100,100,100,103,105,110,108,100,104,108,105,104,103],
            "ST_DIR": [-1,1,1,1,-1,1,1,1,1,-1,1,-1,1],
            "ST": [110,95,110,111,112,100,102,104,105,115,108,116,110],
            "ADX": [18,22,22,27,18,27,45,27,27,18,45,18,27],
        }, index=idx)
        out = compute_buy_cycles(d, years=2)
        events = out["events"]
        self.assertEqual([e["time"] for e in events], [idx[2].strftime("%Y-%m-%d"), idx[7].strftime("%Y-%m-%d"), idx[12].strftime("%Y-%m-%d")])
        self.assertEqual(events[0]["opinion_code"], "STRONG_BUY")
        self.assertEqual(events[0]["exit_time"], idx[6].strftime("%Y-%m-%d"))
        self.assertEqual(events[0]["exit_opinion"], "SELL")
        self.assertAlmostEqual(events[0]["max_return_pct"], 20.0, places=8)
        self.assertAlmostEqual(events[1]["max_return_pct"], 12.0, places=8)
        self.assertAlmostEqual(out["median_max_return_pct"], 16.0, places=8)
        self.assertEqual(out["completed_events"], 2)
        self.assertFalse(events[2]["completed"])

    def test_signal_series_contains_adx_and_opinion(self):
        c = 100 + 12 * np.sin(np.linspace(0, 18*np.pi, 760)) + np.linspace(0, 25, 760)
        sig = signal_series(frame_from_close(c))
        self.assertIn("ADX", sig.columns)
        self.assertIn("opinion_code", sig.columns)
        self.assertIn(str(sig.iloc[-1]["opinion_code"]), {"STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"})

    def test_analyze_chart_has_126_bars_and_adx(self):
        c = 100 + 10 * np.sin(np.linspace(0, 16*np.pi, 720)) + np.linspace(0, 25, 720)
        result = analyze(frame_from_close(c))
        self.assertTrue(result["available"])
        self.assertEqual(len(result["chart"]), 126)
        self.assertIn("adx", result["chart"][-1])
        self.assertEqual(result["period"], 14)
        self.assertEqual(result["multiplier"], 2.0)
        self.assertIn(result["opinion_code"], {"STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"})


if __name__ == "__main__":
    unittest.main()
