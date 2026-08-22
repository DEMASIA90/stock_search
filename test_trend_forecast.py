from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from trend_forecast import forecast, forecast_at


def synthetic_frame(n: int = 360, slope: float = 0.001, seed: int = 7) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-02", periods=n)
    tau = np.arange(n, dtype=float) - (n - 1)
    close = 100.0 * np.exp(slope * tau)
    volume = np.full(n, 1_000_000.0)
    # Slight deterministic variation prevents accidental all-tie behavior.
    volume *= 1.0 + 0.02 * np.sin(np.arange(n) / 11.0)
    return pd.DataFrame({
        "Open": close,
        "High": close * 1.001,
        "Low": close * 0.999,
        "Close": close,
        "Adj Close": close,
        "Volume": volume,
    }, index=idx)


class TrendForecastTests(unittest.TestCase):
    def test_slope_recovery(self):
        df = synthetic_frame(slope=0.001)
        out = forecast(df)
        self.assertTrue(out["forecastable"])
        self.assertAlmostEqual(out["slope_log_per_day"], 0.001, places=8)

    def test_anchor_selection_relative_volume_spikes(self):
        df = synthetic_frame()
        # Forecast window begins at n-252. Plant one dominant relative-volume
        # spike in each 42-session bucket, sufficiently far from its neighbors.
        start = len(df) - 252
        spikes = [start + b * 42 + 20 for b in range(6)]
        for i in spikes:
            df.iloc[i, df.columns.get_loc("Volume")] = 10_000_000.0 + i
        out = forecast(df)
        self.assertTrue(out["forecastable"])
        selected = [pd.Timestamp(a["date"]) for a in out["anchors"]]
        expected = [df.index[i] for i in spikes]
        self.assertEqual(selected, expected)

    def test_half_life_changes_slope(self):
        df = synthetic_frame(slope=0.0)
        n = len(df)
        start = n - 252
        # Older half trends down, newer half trends up. Anchors are forced to
        # representative points by volume spikes so recency weights matter.
        model_pos = np.arange(252)
        piece = np.where(model_pos < 126, -0.002 * model_pos, -0.252 + 0.004 * (model_pos - 126))
        prices = 100.0 * np.exp(piece)
        df.iloc[start:, df.columns.get_loc("Close")] = prices
        df.iloc[start:, df.columns.get_loc("Adj Close")] = prices
        df.iloc[start:, df.columns.get_loc("Open")] = prices
        df.iloc[start:, df.columns.get_loc("High")] = prices * 1.001
        df.iloc[start:, df.columns.get_loc("Low")] = prices * 0.999
        for b in range(6):
            i = start + b * 42 + 20
            df.iloc[i, df.columns.get_loc("Volume")] = 9_000_000.0
        slow = forecast(df, half_life=126)
        fast = forecast(df, half_life=42)
        self.assertTrue(slow["forecastable"] and fast["forecastable"])
        self.assertGreater(abs(fast["slope_log_per_day"] - slow["slope_log_per_day"]), 1e-8)

    def test_no_lookahead(self):
        df = synthetic_frame(n=420, slope=0.0007)
        asof = df.index[360]
        a = forecast_at(df, asof)
        poisoned = df.copy()
        poisoned.loc[poisoned.index > asof, :] = np.nan
        b = forecast_at(poisoned, asof)
        self.assertTrue(a["forecastable"] and b["forecastable"])
        self.assertEqual(a["score"], b["score"])
        self.assertEqual(a["anchors"], b["anchors"])

    def test_projection_starts_from_actual_level(self):
        df = synthetic_frame(slope=0.001)
        # Shift only the last actual price away from the fitted intercept.
        df.iloc[-1, df.columns.get_loc("Close")] *= 1.03
        df.iloc[-1, df.columns.get_loc("Adj Close")] *= 1.03
        out = forecast(df)
        self.assertTrue(out["forecastable"])
        p0 = float(df["Adj Close"].iloc[-1])
        m = out["slope_log_per_day"]
        self.assertAlmostEqual(out["projection"][0]["price"], p0 * math.exp(m), places=5)
        self.assertNotAlmostEqual(out["current_price"], out["fitted_price_tau0"], places=3)

    def test_weight_ratio_cap(self):
        df = synthetic_frame()
        start = len(df) - 252
        for b in range(6):
            i = start + b * 42 + 20
            df.iloc[i, df.columns.get_loc("Volume")] = 3_000_000.0
        # One absurd relative-volume event must not own the WLS.
        i = start + 5 * 42 + 20
        df.iloc[i, df.columns.get_loc("Volume")] = 1_000_000_000.0
        out = forecast(df)
        weights = [a["weight"] for a in out["anchors"]]
        self.assertLessEqual(max(weights) / min(weights), 4.000001)


    def test_fast_backtest_engine_matches_production_score(self):
        from backtest_forecast import FastForecastSeries
        df = synthetic_frame(n=420, slope=0.0008)
        out = forecast(df, half_life=84, n_buckets=6, horizon=20)
        engine = FastForecastSeries.from_frame(df)
        fast_score = engine.score(len(df)-1, 6, 84, 20)
        self.assertTrue(out["forecastable"])
        self.assertAlmostEqual(fast_score, out["score"], places=7)


    def test_today_is_never_an_anchor(self):
        df = synthetic_frame(n=400)
        # Make the final session the largest relative-volume day in the series.
        df.iloc[-1, df.columns.get_loc("Volume")] = 5_000_000_000.0
        out = forecast(df)
        self.assertTrue(out["forecastable"])
        self.assertTrue(all(a["tau"] != 0 for a in out["anchors"]))
        self.assertGreaterEqual(out["last_anchor_gap"], 1)


class BacktestAggregationTests(unittest.TestCase):
    def test_zero_score_is_not_counted_as_a_miss(self):
        from backtest_forecast import _metric_block
        rows = []
        for i in range(20):
            # Ten confident and perfectly correct calls, ten abstentions.
            rows.append({"date": "2025-01-02", "ticker": f"A{i}", "market": "KR",
                         "score": 0.0 if i >= 10 else 0.05,
                         "fwd_adj": 0.03 if i < 10 else -0.02})
        ev = pd.DataFrame(rows)
        m = _metric_block(ev, "score")
        self.assertAlmostEqual(m["hit_rate"], 1.0)
        self.assertAlmostEqual(m["coverage"], 0.5)
        self.assertEqual(m["n_events"], 10)


if __name__ == "__main__":
    unittest.main()
