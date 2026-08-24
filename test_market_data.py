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


    def test_toss_page_surfaces_http_429_without_hiding(self):
        class Resp:
            status_code = 429
            text = '{"message":"rate limited"}'
            def json(self):
                return {"message": "rate limited"}
        with patch.object(md.requests, 'get', return_value=Resp()):
            with self.assertRaises(md.ExactMarketDataError) as ctx:
                md._toss_page('A005930', 5, None, 1)
        self.assertIn('HTTP 429', str(ctx.exception))
        self.assertIn('stop bulk retries', str(ctx.exception))

    def test_toss_group_surfaces_total_transport_failure(self):
        stocks = [self.stock('005930', '005930.KS', 'KOSPI')]
        with patch.object(md, 'fetch_toss_kr_daily', side_effect=md.ExactMarketDataError('HTTP 403 blocked')):
            with self.assertRaises(md.ExactMarketDataError) as ctx:
                md._download_toss_group(stocks, bars=5, timeout=1)
        self.assertIn('all Toss c-chart requests failed', str(ctx.exception))
        self.assertIn('HTTP 403 blocked', str(ctx.exception))

    def test_kr_preflight_prefers_samsung_when_available(self):
        stocks = [
            self.stock('000660', '000660.KS', 'KOSPI'),
            self.stock('005930', '005930.KS', 'KOSPI'),
            self.stock('035420', '035420.KS', 'KOSPI'),
        ]
        seen = []
        def fake_download(sample, category, bars, timeout):
            seen.extend([s.ticker for s in sample])
            idx = pd.DatetimeIndex([pd.Timestamp('2026-08-24')])
            frame = pd.DataFrame({'Open':[1.0],'High':[1.0],'Low':[1.0],'Close':[1.0],'Volume':[1.0]}, index=idx)
            return {s.ticker: frame for s in sample}
        with patch.object(md, 'download_market_frames', side_effect=fake_download):
            usable, total = md.exact_source_preflight(stocks, 'KR', timeout=1)
        self.assertEqual((usable, total), (2, 2))
        self.assertEqual(seen[0], '005930.KS')

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


    def test_tradingview_protocol_uses_distinct_series_key_and_parses_du(self):
        ts = int(pd.Timestamp('2026-08-21 20:00:00', tz='UTC').timestamp())
        du = md._tv_frame(json.dumps({
            'm': 'du',
            'p': ['cs_any', {'sds_1': {'s': [
                {'i': 0, 'v': [ts, 630.0, 635.0, 628.0, 634.0, 1234567]}
            ]}}]
        }, separators=(',', ':')))
        done = md._tv_frame(json.dumps({
            'm': 'series_completed', 'p': ['cs_any', 'sds_1']
        }, separators=(',', ':')))

        class FakeWS:
            def __init__(self):
                self.sent = []
                self.messages = [du, done]
            def settimeout(self, value):
                self.timeout = value
            def send(self, value):
                self.sent.append(value)
            def recv(self):
                return self.messages.pop(0)
            def close(self):
                pass

        fake = FakeWS()
        stock = self.stock('SPY', 'SPY', 'NYSE Arca')
        with patch.object(md, '_tv_connect', return_value=fake):
            got = md.fetch_tradingview_us_batch([stock], bars=5, timeout=2)

        self.assertIn('SPY', got)
        self.assertEqual(len(got['SPY']), 1)
        commands = []
        for framed in fake.sent:
            for body in md._tv_payloads(framed):
                if body.startswith('{'):
                    commands.append(json.loads(body))
        create = next(x for x in commands if x.get('m') == 'create_series')
        self.assertEqual(create['p'][1:4], ['sds_1', 's1', 'sds_sym_1'])
        resolve = next(x for x in commands if x.get('m') == 'resolve_symbol')
        self.assertIn('AMEX:SPY', resolve['p'][2])
        self.assertIn('"adjustment":"splits"', resolve['p'][2])
        self.assertIn('"session":"regular"', resolve['p'][2])

    def test_tradingview_symbol_error_isolated_without_batch_exception(self):
        err = md._tv_frame(json.dumps({
            'm': 'symbol_error',
            'p': ['cs_any', 'sds_sym_1', 'Unknown symbol']
        }, separators=(',', ':')))

        class FakeWS:
            def __init__(self):
                self.sent = []
                self.messages = [err]
            def settimeout(self, value):
                pass
            def send(self, value):
                self.sent.append(value)
            def recv(self):
                return self.messages.pop(0)
            def close(self):
                pass

        with patch.object(md, '_tv_connect', return_value=FakeWS()):
            got = md.fetch_tradingview_us_batch([self.stock('BAD', 'BAD', 'NASDAQ')], bars=5, timeout=2)
        self.assertEqual(got, {})

    def test_tradingview_group_surfaces_total_transport_failure(self):
        stocks = [self.stock('SPY', 'SPY', 'NYSE Arca')]
        with patch.object(md, 'fetch_tradingview_us_batch', side_effect=md.ExactMarketDataError('handshake 403')):
            with self.assertRaises(md.ExactMarketDataError) as ctx:
                md._download_tradingview_group(stocks, bars=5, timeout=1)
        self.assertIn('all TradingView socket groups failed', str(ctx.exception))
        self.assertIn('handshake 403', str(ctx.exception))

    def test_tradingview_frame_uses_utf8_byte_length(self):
        body = '한글'
        framed = md._tv_frame(body)
        self.assertTrue(framed.startswith(f"~m~{len(body.encode('utf-8'))}~m~"))
        self.assertEqual(md._tv_payloads(framed), [body])


    def test_toss_page_downshifts_large_count_after_http_400(self):
        calls = []
        class Resp:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
                self.text = json.dumps(payload)
            def json(self):
                return self._payload
        def fake_get(url, headers=None, timeout=None):
            calls.append(url)
            if 'count=200' in url or 'count=120' in url:
                return Resp(400, {'error': {'statusCode': 400, 'code': '400'}})
            return Resp(200, {'result': {'candles': [
                {'dt':'2026-08-21T00:00:00+09:00','open':100,'high':110,'low':95,'close':108,'volume':1234}
            ]}})
        with patch.object(md.requests, 'get', side_effect=fake_get):
            frame, _ = md._toss_page('A005930', 200, None, 1)
        self.assertEqual(len(frame), 1)
        self.assertTrue(any('count=200' in u for u in calls))
        self.assertTrue(any('count=120' in u for u in calls))
        self.assertTrue(any('count=61' in u for u in calls))

    def test_tradingview_two_symbols_are_serialized_one_series_per_session(self):
        ts = int(pd.Timestamp('2026-08-21 20:00:00', tz='UTC').timestamp())
        def msg(method, params):
            return md._tv_frame(json.dumps({'m': method, 'p': params}, separators=(',', ':')))
        messages = [
            msg('du', ['cs_x', {'sds_1': {'s': [{'i':0,'v':[ts,1,2,0.5,1.5,100]}]}}]),
            msg('series_completed', ['cs_x', 'sds_1']),
            msg('du', ['cs_y', {'sds_2': {'s': [{'i':0,'v':[ts,2,3,1.5,2.5,200]}]}}]),
            msg('series_completed', ['cs_y', 'sds_2']),
        ]
        class FakeWS:
            def __init__(self):
                self.sent=[]; self.messages=list(messages)
            def settimeout(self, value): pass
            def send(self, value): self.sent.append(value)
            def recv(self): return self.messages.pop(0)
            def close(self): pass
        fake=FakeWS()
        stocks=[self.stock('SPY','SPY','NYSE Arca'), self.stock('QQQ','QQQ','NASDAQ')]
        with patch.object(md, '_tv_connect', return_value=fake):
            got=md.fetch_tradingview_us_batch(stocks, bars=5, timeout=8)
        self.assertEqual(set(got), {'SPY','QQQ'})
        commands=[]
        for framed in fake.sent:
            for body in md._tv_payloads(framed):
                if body.startswith('{'):
                    commands.append(json.loads(body))
        methods=[x.get('m') for x in commands]
        first_create=methods.index('create_series')
        first_remove=methods.index('remove_series')
        second_create=methods.index('create_series', first_create+1)
        self.assertLess(first_create, first_remove)
        self.assertLess(first_remove, second_create)

    def test_no_yahoo_ohlc_fallback_marker(self):
        source = Path(md.__file__).read_text(encoding='utf-8').lower()
        self.assertIn('no yahoo ohlc fallback', source)
        self.assertNotIn('yfinance', source)


if __name__ == '__main__':
    unittest.main()
