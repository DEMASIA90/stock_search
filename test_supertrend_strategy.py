import unittest
import numpy as np
import pandas as pd

from supertrend_strategy import supertrend, signal_series, heikin_ashi, backtest_buy_s_to_sell, _opinion_for


def frame_from_close(values):
    idx = pd.bdate_range("2023-01-02", periods=len(values))
    c = np.asarray(values, dtype=float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * 1.01
    l = np.minimum(o, c) * 0.99
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1_000_000}, index=idx)


class SupertrendTests(unittest.TestCase):
    def test_uptrend_finishes_up(self):
        df = frame_from_close(np.linspace(100, 180, 120))
        st = supertrend(df)
        self.assertEqual(int(st["direction"].dropna().iloc[-1]), 1)

    def test_downtrend_finishes_down(self):
        df = frame_from_close(np.linspace(180, 100, 120))
        st = supertrend(df)
        self.assertEqual(int(st["direction"].dropna().iloc[-1]), -1)

    def test_signal_has_p0_after_flip(self):
        c = np.r_[np.linspace(150, 100, 80), np.linspace(100, 160, 80)]
        sig = signal_series(frame_from_close(c))
        self.assertTrue(sig["p0"].notna().any())

    def test_heikin_ashi_length(self):
        df = frame_from_close(np.linspace(100, 120, 30))
        ha = heikin_ashi(df)
        self.assertEqual(len(ha.dropna()), len(df))


    def test_grade_thresholds(self):
        self.assertEqual(_opinion_for(101.0, 100.0, 100.5, 1.0)[0], "BUY_S")
        self.assertEqual(_opinion_for(103.0, 100.0, 100.5, 1.0)[0], "BUY_A")
        self.assertEqual(_opinion_for(107.0, 100.0, 100.5, 1.0)[0], "BUY_B")
        self.assertEqual(_opinion_for(115.0, 100.0, 100.5, 1.0)[0], "BUY_C")
        self.assertEqual(_opinion_for(125.0, 100.0, 100.5, 1.0)[0], "HOLD")

    def test_sell_overrides_grade(self):
        self.assertEqual(_opinion_for(101.0, 100.0, 99.0, -1.0)[0], "SELL")

    def test_p1_below_p0_is_hold(self):
        self.assertEqual(_opinion_for(101.0, 100.0, 99.5, 1.0)[0], "HOLD")

    def test_backtest_is_deterministic(self):
        c = 100 + 12*np.sin(np.linspace(0, 16*np.pi, 700)) + np.linspace(0, 20, 700)
        df = frame_from_close(c)
        a = backtest_buy_s_to_sell(df)
        b = backtest_buy_s_to_sell(df)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
