import unittest
import numpy as np
import pandas as pd

from supertrend_strategy import (
    SUPER_TREND_PERIOD, SUPER_TREND_MULTIPLIER, adx, supertrend,
    add_up_flip_reference, weekly_supertrend_asof_daily, classify_dual_supertrend,
    signal_series, compute_buy_cycles, analyze,
)


def frame_from_close(values, wick=0.012):
    idx = pd.bdate_range("2022-01-03", periods=len(values))
    c = np.asarray(values, dtype=float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1.0 + wick)
    l = np.minimum(o, c) * (1.0 - wick)
    return pd.DataFrame({"Open":o,"High":h,"Low":l,"Close":c,"Volume":1_000_000.0}, index=idx)


def weekly_ohlc(df):
    return df.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()


class DualSupertrendTests(unittest.TestCase):
    def test_parameters(self):
        self.assertEqual(SUPER_TREND_PERIOD, 14)
        self.assertEqual(SUPER_TREND_MULTIPLIER, 2.0)

    def test_tradingview_daily_st_and_adx_warmup(self):
        c = np.linspace(100, 160, 180) + 3*np.sin(np.arange(180)/6)
        df = frame_from_close(c)
        st = supertrend(df, 14, 2.0)
        ax = adx(df, 14, 14)
        self.assertEqual(int(np.flatnonzero(np.isfinite(st["ATR"].to_numpy()))[0]), 13)
        self.assertEqual(int(np.flatnonzero(np.isfinite(ax["ADX"].to_numpy()))[0]), 27)

    def test_previous_down_reference(self):
        idx = pd.bdate_range("2025-01-01", periods=5)
        d = pd.DataFrame({"ST_DIR":[-1,-1,1,1,1],"ST":[110,108,95,108,109]}, index=idx)
        out = add_up_flip_reference(d)
        self.assertEqual(float(out["ST_UP_FLIP_REF"].iloc[2]),108.0)
        self.assertEqual(float(out["ST_UP_FLIP_AGE"].iloc[2]),0.0)
        self.assertEqual(float(out["ST_UP_FLIP_AGE"].iloc[3]),1.0)

    def test_weekly_asof_matches_completed_week_supertrend(self):
        n=760; x=np.arange(n,dtype=float)
        df=frame_from_close(100+0.05*x+8*np.sin(x/21.0), wick=.018)
        asof=weekly_supertrend_asof_daily(df,14,2.0)
        wk=weekly_ohlc(df)
        direct=supertrend(wk,14,2.0)
        # On each final trading day of a completed week, the as-of weekly ST must
        # equal a direct weekly SuperTrend computed through that week.
        for week_end, row in direct.iloc[20:].iterrows():
            dates=df.index[df.index.to_period('W-FRI')==week_end.to_period('W-FRI')]
            if not len(dates):
                continue
            last=dates[-1]
            got=float(asof.loc[last,"W_ST"])
            exp=float(row["ST"])
            if np.isfinite(exp): self.assertAlmostEqual(got,exp,places=10)

    def test_weekly_asof_has_no_future_leakage(self):
        n=720; x=np.arange(n,dtype=float)
        df=frame_from_close(100+0.04*x+7*np.sin(x/17.0))
        cut=650
        a=weekly_supertrend_asof_daily(df.iloc[:cut+1],14,2.0).iloc[-1]
        changed=df.copy()
        changed.iloc[cut+1:, changed.columns.get_loc('High')] *= 5
        changed.iloc[cut+1:, changed.columns.get_loc('Close')] *= .2
        b=weekly_supertrend_asof_daily(changed.iloc[:cut+1],14,2.0).iloc[-1]
        self.assertAlmostEqual(float(a.W_ST), float(b.W_ST), places=12)
        self.assertEqual(int(a.W_ST_DIR), int(b.W_ST_DIR))

    def test_opinion_table(self):
        # Both directions DOWN -> 매도
        self.assertEqual(classify_dual_supertrend(-1,90,100,2,-1,95,100,2)[0],"SELL")
        # Exactly one DOWN -> 매도 고려, even if other timeframe gate passes.
        self.assertEqual(classify_dual_supertrend(1,110,100,2,-1,95,100,2)[0],"SELL_CONSIDER")
        # Both UP: case combinations.
        self.assertEqual(classify_dual_supertrend(1,110,100,2,1,120,110,2)[0],"BUY")
        self.assertEqual(classify_dual_supertrend(1,110,100,2,1,105,110,2)[0],"SHORT_BUY")
        self.assertEqual(classify_dual_supertrend(1,95,100,2,1,120,110,2)[0],"LONG_BUY")
        self.assertEqual(classify_dual_supertrend(1,95,100,2,1,105,110,2)[0],"HOLD")

    def test_backtest_buy_to_sell(self):
        idx=pd.bdate_range("2025-01-01",periods=8)
        d=pd.DataFrame({
            "Open":[100,101,102,103,104,105,106,107],
            "High":[101,103,108,112,115,111,109,108],
            "Low":[99,100,101,102,103,104,105,106],
            "Close":[100,102,105,108,110,109,108,107],
            "opinion_code":["HOLD","BUY","SHORT_BUY","HOLD","SELL_CONSIDER","SELL_CONSIDER","SELL","HOLD"],
        },index=idx)
        out=compute_buy_cycles(d,years=2)
        self.assertEqual(out["completed_events"],1)
        self.assertEqual(out["events"][0]["time"],idx[1].strftime("%Y-%m-%d"))
        self.assertEqual(out["events"][0]["exit_time"],idx[6].strftime("%Y-%m-%d"))
        self.assertAlmostEqual(out["events"][0]["max_return_pct"],(115/102-1)*100,places=10)

    def test_signal_and_analyze_outputs(self):
        n=760; x=np.arange(n,dtype=float)
        df=frame_from_close(100+0.05*x+10*np.sin(x/19.0))
        sig=signal_series(df)
        for col in ("ADX","W_ST","W_ST_DIR","CASE1","CASE2","opinion_code"):
            self.assertIn(col,sig.columns)
        self.assertIn(str(sig.iloc[-1].opinion_code),{"BUY","SHORT_BUY","LONG_BUY","HOLD","SELL_CONSIDER","SELL"})
        result=analyze(df)
        self.assertTrue(result["available"])
        self.assertEqual(len(result["chart"]),126)
        self.assertIn("weekly_supertrend",result["chart"][-1])
        self.assertIn("st_d_direction",result)
        self.assertIn("st_w_direction",result)


if __name__ == '__main__':
    unittest.main()
