import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from pathlib import Path

import market_data as md


class MarketDataAdapterTests(unittest.TestCase):
    def stock(self, symbol='005930', ticker='005930.KS', exchange='KOSPI'):
        return SimpleNamespace(symbol=symbol, ticker=ticker, exchange=exchange)

    def test_market_source_routing(self):
        self.assertEqual(md.market_data_source_for('KR'), 'TOSS_WTS_C_CHART_ADJUSTED')
        self.assertEqual(md.market_data_source_for('KR_ETF'), 'TOSS_WTS_C_CHART_ADJUSTED')
        self.assertEqual(md.market_data_source_for('US'), 'TRADINGVIEW_PUBLIC_CHART_REGULAR_SPLITS')
        self.assertEqual(md.market_data_source_for('US_ETF'), 'TRADINGVIEW_PUBLIC_CHART_REGULAR_SPLITS')

    def test_toss_product_code(self):
        self.assertEqual(md.toss_product_code(self.stock()), 'A005930')
        self.assertEqual(md.toss_product_code(self.stock(symbol='0048J0', ticker='0048J0.KS')), 'A0048J0')

    def test_toss_candle_parser_accepts_wts_and_openapi_keys(self):
        candles = [
            {'dt':'2026-08-21T00:00:00+09:00','open':100,'high':110,'low':95,'close':108,'volume':1234},
            {'timestamp':'2026-08-22T09:00:00+09:00','openPrice':'108','highPrice':'112','lowPrice':'101','closePrice':'105','volume':'2345'},
        ]
        df = md._parse_toss_candles(candles)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.index[0], pd.Timestamp('2026-08-21'))
        self.assertAlmostEqual(float(df.iloc[1]['Close']), 105.0)
        self.assertAlmostEqual(float(df.iloc[1]['Volume']), 2345.0)

    def test_toss_pagination_collects_more_than_500(self):
        idx = pd.bdate_range('2023-01-02', periods=720)
        full = pd.DataFrame({
            'Open': range(1000, 1720),
            'High': range(1001, 1721),
            'Low': range(999, 1719),
            'Close': range(1000, 1720),
            'Volume': 1000,
        }, index=idx)
        newest = full.tail(500)
        older = full.head(220)
        calls = []
        def fake_page(code, count, cursor, timeout):
            calls.append(cursor)
            return (newest, None) if len(calls) == 1 else (older, None)
        with patch.object(md, '_toss_page', side_effect=fake_page):
            got = md.fetch_toss_kr_daily(self.stock(), bars=720, timeout=1)
        self.assertEqual(len(got), 720)
        self.assertTrue(got.index.is_monotonic_increasing)
        self.assertEqual(got.index[0], idx[0])
        self.assertEqual(got.index[-1], idx[-1])
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0])
        self.assertIsNotNone(calls[1])

    def test_tradingview_symbol_mapping(self):
        self.assertEqual(md.tradingview_symbol(self.stock('AAPL','AAPL','NASDAQ')), 'NASDAQ:AAPL')
        self.assertEqual(md.tradingview_symbol(self.stock('JPM','JPM','NYSE')), 'NYSE:JPM')
        self.assertEqual(md.tradingview_symbol(self.stock('SPY','SPY','NYSE Arca')), 'AMEX:SPY')
        self.assertEqual(md.tradingview_symbol(self.stock('BRK.B','BRK.B','NYSE')), 'NYSE:BRK.B')

    def test_tradingview_message_framing_roundtrip(self):
        body = json.dumps({'m':'series_completed','p':['cs_x','ser_0']}, separators=(',',':'))
        framed = md._tv_frame(body)
        self.assertEqual(md._tv_payloads(framed), [body])

    def test_tradingview_timescale_parser(self):
        ts1 = int(pd.Timestamp('2026-08-20 20:00:00', tz='UTC').timestamp())
        ts2 = int(pd.Timestamp('2026-08-21 20:00:00', tz='UTC').timestamp())
        payload = {'s':[
            {'i':0,'v':[ts1,100,105,98,103,1000]},
            {'i':1,'v':[ts2,103,109,101,108,1200]},
        ]}
        df = md._tv_rows_from_series(payload)
        self.assertEqual(len(df), 2)
        self.assertAlmostEqual(float(df.iloc[-1]['Open']), 103.0)
        self.assertAlmostEqual(float(df.iloc[-1]['High']), 109.0)
        self.assertAlmostEqual(float(df.iloc[-1]['Low']), 101.0)
        self.assertAlmostEqual(float(df.iloc[-1]['Close']), 108.0)
        self.assertAlmostEqual(float(df.iloc[-1]['Volume']), 1200.0)

    def test_no_yahoo_ohlc_fallback_marker(self):
        source = Path(md.__file__).read_text(encoding='utf-8').lower()
        self.assertIn('no yahoo ohlc fallback', source)
        self.assertNotIn('yfinance', source)


if __name__ == '__main__':
    unittest.main()
