import unittest
import numpy as np
import pandas as pd

from supertrend_strategy import (
    SUPER_TREND_PERIOD,
    SUPER_TREND_MULTIPLIER,
    ST_LONG_WEEKLY_PERIOD,
    ST_LONG_WEEKLY_MULTIPLIER,
    adx,
    supertrend,
    add_up_flip_reference,
    weekly_supertrend_asof_daily,
    classify_supertrend,
    classify_supertrend_composite,
    signal_series,
    compute_buy_cycles,
    analyze,
    _current_case1_age,
    _current_flag_age,
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


def weekly_ohlc(df):
    return df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()


class CompositeSupertrendTests(unittest.TestCase):
    def test_parameters(self):
        self.assertEqual(SUPER_TREND_PERIOD, 20)
        self.assertEqual(SUPER_TREND_MULTIPLIER, 4.0)
        self.assertEqual(ST_LONG_WEEKLY_PERIOD, 10)
        self.assertEqual(ST_LONG_WEEKLY_MULTIPLIER, 3.0)

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

    def test_original_buy_gate_unchanged(self):
        self.assertEqual(classify_supertrend(1, 110, 100, 0)[0], "BUY")
        self.assertEqual(classify_supertrend(1, 100, 100, 0)[0], "BUY")
        self.assertEqual(classify_supertrend(1, 99.99, 100, 5)[0], "SELL")
        self.assertEqual(classify_supertrend(-1, 120, 100, 5)[0], "SELL")

    def test_composite_opinion_table(self):
        # Both rules true -> Super Buy.
        self.assertEqual(classify_supertrend_composite(1, 120, 110, 1, 100)[0], "SUPER_BUY")
        # ST_LONG only -> Long Buy.
        self.assertEqual(classify_supertrend_composite(1, 120, 130, 1, 100)[0], "LONG_BUY")
        # Original breakout only -> BUY.
        self.assertEqual(classify_supertrend_composite(1, 120, 110, 1, 130)[0], "BUY")
        # Weekly ST must be UP for ST_LONG.
        self.assertEqual(classify_supertrend_composite(1, 120, 130, -1, 100)[0], "SELL")
        # Strict greater-than: equality is not ST_LONG.
        self.assertEqual(classify_supertrend_composite(1, 100, 130, 1, 100)[0], "SELL")
        # Daily ST must be UP.
        self.assertEqual(classify_supertrend_composite(-1, 120, 110, 1, 100)[0], "SELL")

    def test_weekly_asof_matches_direct_weekly_st_10_3(self):
        n = 760
        x = np.arange(n, dtype=float)
        df = frame_from_close(100 + 0.05 * x + 8 * np.sin(x / 21.0), wick=.018)
        asof = weekly_supertrend_asof_daily(df, 10, 3.0)
        wk = weekly_ohlc(df)
        direct = supertrend(wk, 10, 3.0)
        for week_end, row in direct.iloc[16:].iterrows():
            dates = df.index[df.index.to_period('W-FRI') == week_end.to_period('W-FRI')]
            if not len(dates):
                continue
            got = float(asof.loc[dates[-1], "W_ST"])
            exp = float(row["ST"])
            if np.isfinite(exp):
                self.assertAlmostEqual(got, exp, places=10)

    def test_weekly_asof_has_no_future_leakage(self):
        n = 720
        x = np.arange(n, dtype=float)
        df = frame_from_close(100 + 0.04 * x + 7 * np.sin(x / 17.0))
        cut = 650
        a = weekly_supertrend_asof_daily(df.iloc[:cut + 1], 10, 3.0).iloc[-1]
        changed = df.copy()
        changed.iloc[cut + 1:, changed.columns.get_loc('High')] *= 5
        changed.iloc[cut + 1:, changed.columns.get_loc('Close')] *= .2
        b = weekly_supertrend_asof_daily(changed.iloc[:cut + 1], 10, 3.0).iloc[-1]
        self.assertAlmostEqual(float(a.W_ST), float(b.W_ST), places=12)
        self.assertEqual(int(a.W_ST_DIR), int(b.W_ST_DIR))

    def test_signal_age_uses_current_final_state(self):
        idx = pd.to_datetime(["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"])
        d = pd.DataFrame({"SUPER_BUY": [False, False, True, True]}, index=idx)
        signal_date, age_days, age_sessions = _current_flag_age(d, "SUPER_BUY")
        self.assertEqual(signal_date, "2026-08-24")
        self.assertEqual(age_days, 1)
        self.assertEqual(age_sessions, 1)

    def test_case1_age_compatibility(self):
        idx = pd.to_datetime(["2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25"])
        d = pd.DataFrame({"CASE1": [False, True, True, True]}, index=idx)
        signal_date, age_days, age_sessions = _current_case1_age(d)
        self.assertEqual(signal_date, "2026-08-21")
        self.assertEqual(age_days, 4)
        self.assertEqual(age_sessions, 2)

    def test_backtest_enters_on_any_buy_family_signal(self):
        idx = pd.bdate_range("2025-01-01", periods=8)
        d = pd.DataFrame(
            {
                "Open": [100, 101, 102, 103, 104, 105, 106, 107],
                "High": [101, 103, 108, 112, 115, 111, 109, 108],
                "Low": [99, 100, 101, 102, 103, 104, 105, 106],
                "Close": [100, 102, 105, 108, 110, 109, 108, 107],
                "opinion_code": ["SELL", "LONG_BUY", "SUPER_BUY", "BUY", "BUY", "BUY", "SELL", "SELL"],
            },
            index=idx,
        )
        out = compute_buy_cycles(d, years=2)
        self.assertEqual(out["completed_events"], 1)
        self.assertEqual(out["events"][0]["time"], idx[1].strftime("%Y-%m-%d"))
        self.assertEqual(out["events"][0]["exit_time"], idx[6].strftime("%Y-%m-%d"))
        self.assertAlmostEqual(out["events"][0]["max_return_pct"], (115 / 102 - 1) * 100, places=10)

    def test_signal_and_analyze_outputs(self):
        n = 760
        x = np.arange(n, dtype=float)
        df = frame_from_close(100 + 0.05 * x + 10 * np.sin(x / 19.0))
        sig = signal_series(df)
        for col in ("ADX", "ST", "ST_DIR", "W_ST", "W_ST_DIR", "BASE_BUY", "ST_LONG", "SUPER_BUY", "opinion_code"):
            self.assertIn(col, sig.columns)
        self.assertIn(str(sig.iloc[-1].opinion_code), {"SUPER_BUY", "LONG_BUY", "BUY", "SELL"})

        result = analyze(df)
        self.assertTrue(result["available"])
        self.assertEqual(result["period"], 20)
        self.assertEqual(result["multiplier"], 4.0)
        self.assertEqual(result["st_long_weekly_period"], 10)
        self.assertEqual(result["st_long_weekly_multiplier"], 3.0)
        self.assertEqual(len(result["chart"]), 126)
        self.assertIn("weekly_supertrend", result["chart"][-1])
        self.assertIn("st_w_long_direction", result)
        self.assertIn("st_long", result)
        self.assertIn("super_buy", result)
        if result["opinion_code"] in {"SUPER_BUY", "LONG_BUY", "BUY"}:
            self.assertRegex(result["opinion_label"], r"^(Super Buy|Long Buy|BUY) \(\+\d+\)$")


if __name__ == "__main__":
    unittest.main()
