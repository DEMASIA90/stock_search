from __future__ import annotations

import unittest
from datetime import date, datetime
from pathlib import Path

from src.market_indicators import bollinger_state, technical_chart_series, technical_snapshot, watchlist_sort_key
from src.models import Holding
from src.nh_client import NhReadOnlyClient, normalize_cash_flows
from src.portfolio import (
    backfill_daily_realized_history,
    combine_account_totals,
    compact_yield_history_daily,
    decrypt_envelope,
    encrypt_payload,
    holdings_for_web,
    mask_account,
    merge_persistent_events,
    monthly_realized_performance,
    normalize_overseas_daily_transactions,
    normalize_total_transactions,
    portfolio_totals,
    realized_account_date,
    realized_summary,
    reconcile_us_realized_events,
    sanitize_legacy_realized_events,
    update_snapshot_history,
    update_yield_history,
)


class PortfolioTests(unittest.TestCase):
    def test_encryption_round_trip_and_wrong_password(self) -> None:
        payload = {"updated_at": "2026-09-18T12:00:00+09:00", "holdings": [{"name": "테스트"}]}
        encrypted = encrypt_payload(payload, "2580")
        self.assertNotIn("테스트", str(encrypted))
        self.assertEqual(decrypt_envelope(encrypted, "2580"), payload)
        with self.assertRaises(Exception):
            decrypt_envelope(encrypted, "2581")
        with self.assertRaises(ValueError):
            encrypt_payload(payload, "12345")
        with self.assertRaises(ValueError):
            encrypt_payload(payload, "abcd")

    def test_holdings_and_totals(self) -> None:
        holdings = holdings_for_web([
            Holding("KR", "005930", "삼성전자", 2, 70000, 75000, 150000, 10000, 7.14, "KRW"),
            Holding("US", "AAPL", "Apple", 1, 200, 210, 290000, 15000, 5.45, "USD"),
        ])
        totals = portfolio_totals(holdings, [
            {"side": "BUY", "amount": 100000, "currency": "KRW"},
            {"side": "SELL", "amount": 120, "currency": "USD"},
        ])
        self.assertEqual(totals["evaluation_krw"], 440000)
        self.assertEqual(totals["pnl_krw"], 25000)
        self.assertEqual(totals["holding_count"], 2)
        self.assertEqual(totals["buy_krw"], 100000)
        self.assertEqual(totals["sell_usd"], 120)

    def test_transactions_and_history(self) -> None:
        rows = [{
            "ral_trd_dt": "20260918", "trd_sno": "1", "iem_llf_cd": "01",
            "iem_cd": "005930", "iem_nm": "삼성전자", "act_trd_tp_nm": "매수",
            "trd_qty": "2", "trd_uit_pr": "70,000", "trd_amt": "140000", "cur_cd": "KRW",
        }]
        trades = normalize_total_transactions(rows)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["side"], "BUY")
        history = update_snapshot_history([], {"evaluation_krw": 1000}, datetime(2026, 9, 18, 9, 0))
        history = update_snapshot_history(history, {"evaluation_krw": 1100}, datetime(2026, 9, 18, 9, 0))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["evaluation_krw"], 1100)

    def test_market_indicators_and_score(self) -> None:
        rising = [
            {"date": f"2025{i // 28 + 1:02d}{i % 28 + 1:02d}", "open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i}
            for i in range(260)
        ]
        result = technical_snapshot(rising)
        self.assertEqual(result["st_10_3"], "UP")
        self.assertEqual(result["st_20_4"], "UP")
        self.assertGreater(result["ma60_slope_pct"], 0)
        self.assertGreater(result["ma200_slope_pct"], 0)
        self.assertEqual(result["score"], 30)
        self.assertEqual(bollinger_state([100.0] * 20)["label"], "중단")

        rows = [
            {"code": "HIGH", "band": "하단", "score": 30, "band_position": 0.25},
            {"code": "LOW", "band": "최하단", "score": 0, "band_position": 0.1},
        ]
        self.assertEqual(sorted(rows, key=watchlist_sort_key)[0]["code"], "LOW")

    def test_technical_chart_series_contains_candles_bollinger_and_supertrend(self) -> None:
        bars = [
            {
                "date": f"2026{(index // 28) + 1:02d}{(index % 28) + 1:02d}",
                "open": 100 + index * 0.5,
                "high": 102 + index * 0.5,
                "low": 99 + index * 0.5,
                "close": 101 + index * 0.5,
            }
            for index in range(180)
        ]
        chart = technical_chart_series(bars, max_points=120)
        self.assertEqual(len(chart), 120)
        self.assertIn("bb_upper", chart[-1])
        self.assertIn("bb_lower", chart[-1])
        self.assertIn("st_14_3", chart[-1])
        self.assertIn(chart[-1]["st_trend"], {"UP", "DOWN"})
        self.assertGreater(chart[-1]["high"], chart[-1]["low"])

    def test_yield_history_realized_and_cash_flow(self) -> None:
        events = merge_persistent_events(
            [{"id": "KR:1", "date": "20260901", "realized_pnl_krw": 100, "return_pct": 5}],
            [
                {"id": "KR:1", "date": "20260901", "realized_pnl_krw": 120, "return_pct": 6},
                {"id": "US:2", "date": "20260902", "realized_pnl_krw": -20, "return_pct": -2},
            ],
        )
        summary = realized_summary(events)
        self.assertEqual(summary["cumulative_realized_krw"], 100)
        self.assertEqual(summary["trade_count"], 2)
        flows = normalize_cash_flows([
            {"act_trd_dtl_cd": "AA", "trd_dt": "20260903", "trd_sno": "1", "xcl_amt": "1,000", "cur_cd": "KRW"},
            {"act_trd_dtl_cd": "BB", "trd_dt": "20260904", "trd_sno": "2", "xcl_amt": "200", "cur_cd": "KRW"},
        ])
        history = update_yield_history(
            [], totals={"total_asset_krw": 10000, "evaluation_krw": 9000},
            realized=summary, cash_flows=flows, at=datetime(2026, 9, 18, 10, 0),
        )
        self.assertEqual(history[0]["net_cash_flow_krw"], 800)
        monthly = monthly_realized_performance(events, history)
        self.assertAlmostEqual(monthly[0]["return_pct"], 1.0)

    def test_realized_history_backfills_pnl_without_fake_asset_values(self) -> None:
        events = [
            {"id": "KR:1", "date": "20260902", "realized_pnl_krw": 100},
            {"id": "US:2", "date": "20260903", "realized_pnl_krw": -25},
        ]
        history = backfill_daily_realized_history(
            [], realized_events=events, cash_flows=[],
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 4),
        )
        self.assertEqual(len(history), 3)
        self.assertIsNone(history[0]["total_asset_krw"])
        self.assertFalse(history[0]["asset_recorded"])
        self.assertEqual(history[1]["cumulative_realized_krw"], 100)
        self.assertEqual(history[2]["cumulative_realized_krw"], 75)

        summary = realized_summary(events)
        history = update_yield_history(
            history, totals={"total_asset_krw": 5000, "evaluation_krw": 4500},
            realized=summary, cash_flows=[], at=datetime(2026, 9, 4, 10, 0),
        )
        self.assertEqual(history[-1]["total_asset_krw"], 5000)
        self.assertTrue(history[-1]["asset_recorded"])
        monthly = monthly_realized_performance(events, history)
        self.assertAlmostEqual(monthly[0]["return_pct"], 75 / 5000 * 100)

    def test_us_period_summary_date_drives_detail_lookup(self) -> None:
        class FakeClient(NhReadOnlyClient):
            @property
            def environment(self) -> str:
                return "live"

            def _api(self, path: str, payload=None):
                if path.endswith("/tradingPnl"):
                    return {"Output_1": []}
                if path.endswith("/periodPnl"):
                    self.assertEqual(payload["iqr_dit"], "2")
                    self.assertEqual(payload["trd_cur_cd"], "USD")
                    self.assertEqual(payload["fc_sec_trd_nat_cd"], "200")
                    if "iem_cd" in payload:
                        raise AssertionError("Blank iem_cd must not be sent")
                    # Official schema: Output_1 has no iem_cd / iem_nm.
                    return {"Output_1": [{
                        "orr_dt": "20260918", "sll_qty": "2",
                        "fc_rzt_pls": "28000", "fc_rzt_pft_rt": "5.0",
                    }]}
                if path.endswith("/periodPnlDetail"):
                    self.assertEqual(payload["orr_dt"], "20260918")
                    self.assertEqual(payload["iqr_dit"], "2")
                    self.assertEqual(payload["trd_cur_cd"], "USD")
                    self.assertEqual(payload["fc_sec_trd_nat_cd"], "200")
                    if "iem_cd" in payload:
                        raise AssertionError("Blank iem_cd must not be sent")
                    return {"Output_0": [{
                        "iem_cd": "AAPL", "iem_nm": "Apple", "sll_qty": "2",
                        "byn_uit_pr": "200", "sll_uit_pr": "210",
                        "fc_rzt_pls": "28000", "fc_rzt_pft_rt": "5.0",
                    }]}
                raise AssertionError(path)

            def assertEqual(self, left, right):
                if left != right:
                    raise AssertionError((left, right))

        events, warnings, diagnostics = FakeClient().realized_pnl_history(
            "123", datetime(2026, 9, 18), datetime(2026, 9, 18)
        )
        us = [item for item in events if item["market"] == "US"]
        self.assertEqual(len(us), 1)
        self.assertEqual(us[0]["code"], "AAPL")
        self.assertEqual(us[0]["realized_pnl_krw"], 28000)
        self.assertEqual(us[0]["currency"], "USD")
        self.assertEqual(diagnostics["us_period_rows"], 1)
        self.assertEqual(diagnostics["us_period_sale_days"], 1)
        self.assertEqual(diagnostics["us_detail_calls"], 1)
        self.assertEqual(diagnostics["us_events"], 1)
        self.assertFalse(any("미국주식 실현손익 조회 실패" in warning for warning in warnings))

    def test_us_detail_missing_pnl_never_becomes_fake_zero(self) -> None:
        class FakeClient(NhReadOnlyClient):
            @property
            def environment(self) -> str:
                return "live"

            def _api(self, path: str, payload=None):
                if path.endswith("/tradingPnl"):
                    return {"Output_1": []}
                if path.endswith("/periodPnl"):
                    return {"Output_1": [{"orr_dt": "20260918"}]}
                if path.endswith("/periodPnlDetail"):
                    return {"Output_0": [{
                        "iem_cd": "CURE", "iem_nm": "Direxion Healthcare Bull 3X",
                        "sll_qty": "16", "sll_uit_pr": "126.68",
                        # fc_rzt_pls intentionally missing
                    }]}
                raise AssertionError(path)

        trades = [{
            "id": "USDAILY:1", "date": "20260918", "market": "US", "code": "CURE",
            "name": "Direxion Healthcare Bull 3X", "side": "SELL", "qty": 16,
            "price": 126.68, "currency": "USD",
        }]
        events, _, diagnostics = FakeClient().realized_pnl_history(
            "123", datetime(2026, 9, 18), datetime(2026, 9, 18), trades=trades
        )
        us = [item for item in events if item["market"] == "US"]
        self.assertEqual(len(us), 1)
        self.assertIsNone(us[0]["realized_pnl_krw"])
        self.assertFalse(us[0]["pnl_available"])
        self.assertEqual(diagnostics["us_pnl_unavailable_events"], 1)

    def test_us_sell_trade_drives_detail_when_period_is_empty(self) -> None:
        class FakeClient(NhReadOnlyClient):
            @property
            def environment(self) -> str:
                return "live"

            def _api(self, path: str, payload=None):
                if path.endswith("/tradingPnl"):
                    return {"Output_1": []}
                if path.endswith("/periodPnl"):
                    self.assertEqual(payload["iqr_dit"], "2")
                    self.assertEqual(payload["trd_cur_cd"], "USD")
                    self.assertEqual(payload["fc_sec_trd_nat_cd"], "200")
                    return {"Output_1": [], "rsp_msg": "정상처리"}
                if path.endswith("/periodPnlDetail"):
                    self.assertEqual(payload["orr_dt"], "20260918")
                    self.assertEqual(payload["iqr_dit"], "2")
                    self.assertEqual(payload["trd_cur_cd"], "USD")
                    self.assertEqual(payload["fc_sec_trd_nat_cd"], "200")
                    return {"Output_0": [{
                        "iem_cd": "AAPL", "iem_nm": "Apple", "sll_qty": "2",
                        "sll_uit_pr": "210", "byn_uit_pr": "200",
                        "fc_rzt_pls": "28000", "fc_rzt_pft_rt": "5.0",
                    }]}
                raise AssertionError(path)

            def assertEqual(self, left, right):
                if left != right:
                    raise AssertionError((left, right))

        trades = [{
            "id": "COMMON:1", "date": "20260918", "market": "US", "code": "AAPL",
            "name": "Apple", "side": "SELL", "qty": 2, "price": 210, "currency": "USD",
        }]
        events, warnings, diagnostics = FakeClient().realized_pnl_history(
            "123", datetime(2026, 9, 18), datetime(2026, 9, 18), trades=trades
        )
        us = [item for item in events if item["market"] == "US"]
        self.assertEqual(len(us), 1)
        self.assertEqual(us[0]["code"], "AAPL")
        self.assertEqual(us[0]["realized_pnl_krw"], 28000)
        self.assertTrue(us[0]["pnl_available"])
        self.assertEqual(diagnostics["us_period_rows"], 0)
        self.assertEqual(diagnostics["us_detail_calls"], 1)
        self.assertEqual(diagnostics["us_pnl_unavailable_events"], 0)
        self.assertTrue(any("상세조회로 보완" in warning for warning in warnings))

    def test_us_sell_trade_is_preserved_when_both_pnl_apis_are_empty(self) -> None:
        class FakeClient(NhReadOnlyClient):
            @property
            def environment(self) -> str:
                return "live"

            def _api(self, path: str, payload=None):
                if path.endswith("/tradingPnl"):
                    return {"Output_1": []}
                if path.endswith("/periodPnl"):
                    return {"Output_1": []}
                if path.endswith("/periodPnlDetail"):
                    return {"Output_0": []}
                raise AssertionError(path)

        trades = [{
            "id": "COMMON:1", "date": "20260918", "market": "US", "code": "NVDA",
            "name": "NVIDIA", "side": "SELL", "qty": 3, "price": 175.5, "currency": "USD",
        }]
        events, warnings, diagnostics = FakeClient().realized_pnl_history(
            "123", datetime(2026, 9, 18), datetime(2026, 9, 18), trades=trades
        )
        us = [item for item in events if item["market"] == "US"]
        self.assertEqual(len(us), 1)
        self.assertEqual(us[0]["code"], "NVDA")
        self.assertEqual(us[0]["qty"], 3)
        self.assertEqual(us[0]["sell_price"], 175.5)
        self.assertIsNone(us[0]["realized_pnl_krw"])
        self.assertFalse(us[0]["pnl_available"])
        self.assertEqual(diagnostics["us_transaction_fallback_events"], 1)
        self.assertEqual(diagnostics["us_pnl_unavailable_events"], 1)
        self.assertTrue(any("손익" in warning and "—" in warning for warning in warnings))

        summary = realized_summary(us)
        self.assertEqual(summary["trade_count"], 1)
        self.assertEqual(summary["pnl_trade_count"], 0)
        self.assertEqual(summary["cumulative_realized_krw"], 0)
        self.assertEqual(monthly_realized_performance(us, []), [])

    def test_us_pnl_api_diagnostics_keep_response_metadata_without_values(self) -> None:
        class FakeClient(NhReadOnlyClient):
            @property
            def environment(self) -> str:
                return "live"

            def _api(self, path: str, payload=None):
                if path.endswith("/tradingPnl"):
                    return {"Output_1": []}
                if path.endswith("/periodPnl"):
                    return {
                        "rsp_cd": "00000", "rsp_msg": "정상처리",
                        "Output_1": [{"orr_dt": "20260918"}],
                    }
                if path.endswith("/periodPnlDetail"):
                    return {
                        "rsp_cd": "00000", "rsp_msg": "정상처리",
                        "Output_0": [{
                            "iem_cd": "AAPL", "iem_nm": "Apple", "sll_qty": "2",
                            "byn_uit_pr": "200", "sll_uit_pr": "210",
                            "fc_rzt_pls": "28000", "fc_rzt_pft_rt": "5.0",
                        }],
                    }
                raise AssertionError(path)

        events, _, diagnostics = FakeClient().realized_pnl_history(
            "123", datetime(2026, 9, 18), datetime(2026, 9, 18)
        )
        self.assertEqual(len([item for item in events if item["market"] == "US"]), 1)
        self.assertEqual(diagnostics["us_period_rsp_cd"], "00000")
        self.assertEqual(diagnostics["us_period_rsp_msg"], "정상처리")
        self.assertEqual(diagnostics["us_detail_rsp_cd"], "00000")
        self.assertEqual(diagnostics["us_detail_rsp_msg"], "정상처리")
        self.assertIn("fc_rzt_pls", diagnostics["us_detail_first_fields"])
        self.assertEqual(diagnostics["us_pnl_resolved_events"], 1)

    def test_overseas_daily_transaction_and_pagination_are_preserved(self) -> None:
        rows = [{
            "trd_dt": "20260918", "trd_sno": "11", "act_trd_tp_nm": "매도",
            "iem_krl_nm": "애플", "iem_cd": "INTERNAL-AAPL", "oss_iem_cd": "AAPL", "trd_qty": "2",
            "trd_uit_pr": "210.5", "fc_trd_amt": "421", "krw_trd_amt": "590000",
        }]
        normalized = normalize_overseas_daily_transactions(rows)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["side"], "SELL")
        self.assertEqual(normalized[0]["market"], "US")
        self.assertEqual(normalized[0]["price"], 210.5)
        self.assertEqual(normalized[0]["amount_krw"], 590000)

        class FakeClient(NhReadOnlyClient):
            @property
            def environment(self) -> str:
                return "live"

            def _api_pages(self, path: str, payload=None, *, max_pages=50):
                if path.endswith("/totalTransaction"):
                    return [
                        {"Output_0": [{
                            "ral_trd_dt": "20260918", "trd_sno": "1", "iem_llf_cd": "01",
                            "iem_cd": "005930", "iem_nm": "삼성전자", "act_trd_tp_nm": "매수",
                            "trd_qty": "1", "trd_uit_pr": "70000", "trd_amt": "70000", "cur_cd": "KRW",
                        }]},
                        {"Output_0": [{
                            "ral_trd_dt": "20260917", "trd_sno": "2", "iem_llf_cd": "01",
                            "iem_cd": "000660", "iem_nm": "SK하이닉스", "act_trd_tp_nm": "매도",
                            "trd_qty": "1", "trd_uit_pr": "200000", "trd_amt": "200000", "cur_cd": "KRW",
                        }]},
                    ]
                if path.endswith("/dailyTransaction"):
                    if payload["act_trd_cfc_cd"] == "05":
                        return [{"Output_0": [{
                            "trd_dt": "20260918", "trd_sno": "10",
                            "iem_krl_nm": "엔비디아", "iem_cd": "NVDA",
                            "trd_qty": "1", "trd_uit_pr": "175",
                        }]}]
                    if payload["act_trd_cfc_cd"] == "06":
                        return [{"Output_0": [{
                            "trd_dt": "20260918", "trd_sno": "11",
                            "iem_krl_nm": "애플", "iem_cd": "AAPL",
                            "trd_qty": "2", "trd_uit_pr": "210",
                        }]}]
                    raise AssertionError(payload)
                raise AssertionError(path)

        trades, warnings, diagnostics = FakeClient().transaction_history(
            "123", datetime(2026, 9, 1), datetime(2026, 9, 18)
        )
        self.assertFalse(warnings)
        self.assertEqual(diagnostics["common_pages"], 2)
        self.assertEqual(diagnostics["us_daily_pages"], 2)
        self.assertEqual(diagnostics["us_daily_trades"], 2)
        self.assertEqual(diagnostics["us_daily_sell_trades"], 1)
        self.assertEqual({item["code"] for item in trades}, {"005930", "000660", "NVDA", "AAPL"})

    def test_us_detail_replaces_fallback_across_korea_us_date_boundary(self) -> None:
        class FakeClient(NhReadOnlyClient):
            @property
            def environment(self) -> str:
                return "live"

            def _api_pages(self, path: str, payload=None, *, max_pages=50):
                if path.endswith("/periodPnl"):
                    return [{"rsp_cd": "00166", "rsp_msg": "조회가 완료되었습니다.",
                             "Output_1": [{"orr_dt": "20260918"}]}]
                if path.endswith("/periodPnlDetail"):
                    if payload["orr_dt"] == "20260918":
                        return [{"rsp_cd": "00166", "rsp_msg": "조회가 완료되었습니다.",
                                 "Output_0": [{
                                     "iem_cd": "CURE", "iem_nm": "Direxion Healthcare Bull 3X",
                                     "sll_qty": "16", "sll_uit_pr": "126.68",
                                     "byn_uit_pr": "120", "fc_rzt_pls": "152000",
                                     "fc_rzt_pft_rt": "5.56",
                                 }]}]
                    return [{"rsp_cd": "00166", "rsp_msg": "조회가 완료되었습니다.", "Output_0": []}]
                raise AssertionError((path, payload))

            def _api(self, path: str, payload=None):
                if path.endswith("/tradingPnl"):
                    return {"Output_1": []}
                raise AssertionError(path)

        # Overseas dailyTransaction can land on the next Korea calendar date.
        trades = [{
            "id": "USDAILY:20260919:1:CURE:SELL", "date": "20260919",
            "market": "US", "code": "CURE", "name": "Direxion Healthcare Bull 3X",
            "side": "SELL", "qty": 16, "price": 126.68, "currency": "USD",
        }]
        events, _, diagnostics = FakeClient().realized_pnl_history(
            "123", datetime(2026, 9, 18), datetime(2026, 9, 19), trades=trades
        )
        us = [item for item in events if item["market"] == "US"]
        self.assertEqual(len(us), 1)
        self.assertEqual(us[0]["source"], "period_pnl_detail")
        self.assertEqual(us[0]["date"], "20260918")
        self.assertEqual(us[0]["trade_date"], "20260918")
        self.assertEqual(us[0]["account_date"], "20260919")
        self.assertEqual(us[0]["realized_pnl_krw"], 152000)
        self.assertEqual(diagnostics["us_pnl_resolved_events"], 1)
        self.assertEqual(diagnostics["us_pnl_unavailable_events"], 0)
        self.assertEqual(diagnostics["us_transaction_fallback_events"], 0)

    def test_persisted_us_fallback_is_removed_when_detail_row_arrives(self) -> None:
        rows = reconcile_us_realized_events([
            {
                "id": "US:20260919:CURE", "date": "20260919", "market": "US",
                "code": "CURE", "name": "Direxion Healthcare Bull 3X", "qty": 16,
                "sell_price": 126.68, "realized_pnl_krw": None, "pnl_available": False,
                "source": "transaction_fallback",
            },
            {
                "id": "US:20260918:CURE", "date": "20260918", "market": "US",
                "code": "CURE", "name": "Direxion Healthcare Bull 3X", "qty": 16,
                "sell_price": 126.68, "realized_pnl_krw": 152000, "pnl_available": True,
                "source": "period_pnl_detail",
            },
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "period_pnl_detail")
        self.assertEqual(rows[0]["realized_pnl_krw"], 152000)

    def test_realized_account_date_uses_korea_account_day(self) -> None:
        self.assertEqual(
            realized_account_date({
                "market": "US", "date": "20260918", "source": "period_pnl_detail",
            }),
            "20260919",
        )
        self.assertEqual(
            realized_account_date({
                "market": "US", "date": "20260919", "source": "transaction_fallback",
            }),
            "20260919",
        )
        self.assertEqual(
            realized_account_date({
                "market": "KR", "date": "20260919", "source": "trading_pnl",
            }),
            "20260919",
        )

    def test_backfill_uses_account_date_for_us_realized_pnl(self) -> None:
        events = [{
            "id": "US:1", "market": "US", "date": "20260918",
            "account_date": "20260919", "source": "period_pnl_detail",
            "realized_pnl_krw": 1000, "pnl_available": True,
        }]
        history = backfill_daily_realized_history(
            [], realized_events=events, cash_flows=[],
            start_date=date(2026, 9, 18), end_date=date(2026, 9, 21),
        )
        by_day = {item["snapshot_date"]: item for item in history}
        self.assertEqual(by_day["20260918"]["cumulative_realized_krw"], 0)
        self.assertEqual(by_day["20260919"]["cumulative_realized_krw"], 1000)

    def test_monthly_realized_uses_account_date_at_us_month_boundary(self) -> None:
        events = [{
            "id": "US:SEP30", "market": "US", "date": "20260930",
            "source": "period_pnl_detail", "realized_pnl_krw": 500,
            "pnl_available": True,
        }]
        history = [{
            "at": "2026-10-01T09:00:00+09:00", "snapshot_date": "20261001",
            "total_asset_krw": 10000, "asset_recorded": True,
        }]
        monthly = monthly_realized_performance(events, history)
        self.assertEqual(monthly[0]["month"], "2026-10")
        self.assertAlmostEqual(monthly[0]["return_pct"], 5.0)

    def test_yield_history_records_seoul_snapshot_date(self) -> None:
        history = update_yield_history(
            [], totals={"total_asset_krw": 10000, "evaluation_krw": 9000},
            realized={"cumulative_realized_krw": 0}, cash_flows=[],
            at=datetime.fromisoformat("2026-09-18T15:17:00+00:00"),
        )
        self.assertEqual(history[0]["snapshot_date"], "20260919")
        self.assertTrue(str(history[0]["at"]).endswith("+09:00"))

    def test_total_asset_prefers_integrated_asset_status_and_keeps_cash(self) -> None:
        base = {"evaluation_krw": 10_000_000, "pnl_krw": 500_000}
        totals = combine_account_totals(base, {
            "total_asset_krw": 12_500_000,
            "evaluation_krw": 10_500_000,
            "cash_krw": 2_000_000,
            "unrealized_pnl_krw": 600_000,
        })
        self.assertEqual(totals["total_asset_krw"], 12_500_000)
        self.assertEqual(totals["cash_krw"], 2_000_000)

        fallback = combine_account_totals(base, {
            "total_asset_krw": 0,
            "evaluation_krw": 10_500_000,
            "cash_krw": 2_000_000,
        })
        self.assertEqual(fallback["total_asset_krw"], 12_500_000)

    def test_one_year_history_query_is_chunked(self) -> None:
        root = Path(__file__).resolve().parent.parent
        source = (root / "update_portfolio.py").read_text(encoding="utf-8")
        self.assertIn("chunk_days: int = 30", source)
        self.assertIn("cursor = chunk_end + timedelta(days=1)", source)
        self.assertIn("history_days: int = 365", source)

    def test_manual_bootstrap_and_scheduled_refresh_windows(self) -> None:
        root = Path(__file__).resolve().parent.parent
        workflow = (root / ".github" / "workflows" / "update-and-deploy.yml").read_text(encoding="utf-8")
        self.assertIn("update_portfolio.py --non-interactive --history-days 1", workflow)
        batch = (root / "publish_update.bat").read_text(encoding="ascii")
        self.assertIn("update_portfolio.py --history-days 365", batch)

    def test_yield_ui_uses_week_slider_and_compact_realized_rows(self) -> None:
        root = Path(__file__).resolve().parent.parent
        html = (root / "docs" / "index.html").read_text(encoding="utf-8")
        app = (root / "docs" / "assets" / "app.mjs").read_text(encoding="utf-8")
        self.assertIn('id="yieldWeekSlider"', html)
        self.assertIn('min="1" max="156" step="1" value="4"', html)
        self.assertIn('id="yieldRangeLabel">최근 4주', html)
        self.assertLess(html.index('id="holdingsBody"'), html.index('id="realizedBody"'))
        self.assertIn('class="realized-compact-list" id="realizedBody"', html)
        self.assertIn("rows.slice(0, 20)", app)
        self.assertIn('let selectedYieldWeeks = 4', app)

    def test_yield_history_compacts_hourly_asset_snapshots_to_one_per_day(self) -> None:
        rows = [
            {"at": "2026-09-20T09:17:00+09:00", "snapshot_date": "20260920", "total_asset_krw": 100, "asset_recorded": True, "kind": "asset_snapshot"},
            {"at": "2026-09-20T18:17:00+09:00", "snapshot_date": "20260920", "total_asset_krw": 120, "asset_recorded": True, "kind": "asset_snapshot"},
            {"at": "2026-09-20T23:59:00+09:00", "snapshot_date": "20260920", "total_asset_krw": None, "asset_recorded": False, "kind": "realized_daily"},
            {"at": "2026-09-21T09:17:00+09:00", "snapshot_date": "20260921", "total_asset_krw": 130, "asset_recorded": True, "kind": "asset_snapshot"},
        ]
        compacted = compact_yield_history_daily(rows)
        assets = [row for row in compacted if row.get("asset_recorded") is not False and row.get("kind") != "realized_daily"]
        self.assertEqual(len(assets), 2)
        self.assertEqual(assets[0]["total_asset_krw"], 120)
        self.assertEqual(assets[1]["total_asset_krw"], 130)
        self.assertEqual(sum(1 for row in compacted if row.get("kind") == "realized_daily"), 1)

    def test_account_mask(self) -> None:
        self.assertEqual(mask_account("123-45-678901"), "***-***-8901")

    def test_no_trading_ui_or_execution_code(self) -> None:
        root = Path(__file__).resolve().parent.parent
        forbidden = ("PyQt6", "cashBuy", "cashSell", "place_order", "auto_trading")
        for path in list((root / "src").rglob("*.py")) + list((root / "docs").rglob("*.mjs")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{token} remains in {path}")

    def test_legacy_us_transaction_fallback_zero_is_migrated_to_unknown(self) -> None:
        rows = sanitize_legacy_realized_events([{
            "id": "US:20260918:CURE", "date": "20260918", "market": "US",
            "code": "CURE", "source": "transaction_fallback",
            "realized_pnl_krw": 0, "return_pct": 0,
        }])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["realized_pnl_krw"])
        self.assertIsNone(rows[0]["return_pct"])
        self.assertFalse(rows[0]["pnl_available"])

    def test_windows_batch_files_are_ascii_crlf(self) -> None:
        root = Path(__file__).resolve().parent.parent
        batch_files = list(root.glob("*.bat"))
        self.assertTrue(batch_files)
        for path in batch_files:
            data = path.read_bytes()
            data.decode("ascii")
            self.assertIn(b"\r\n", data, f"CRLF line endings missing: {path.name}")
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""), f"Bare LF found: {path.name}")

    def test_legacy_stock_scanner_files_are_removed(self) -> None:
        root = Path(__file__).resolve().parent.parent
        legacy_paths = (
            "scanner.py", "market_data.py", "universe.py", "trend_forecast.py",
            "supertrend_strategy.py", "hydrate_data.py", "capacitor.config.json",
            "package.json", "docs/app.js", "docs/sw.js", "docs/pwa.js",
            "docs/news-config.js", "news_proxy/Code.gs",
        )
        for relative in legacy_paths:
            self.assertFalse((root / relative).exists(), f"Legacy file remains: {relative}")

        workflow = (root / ".github" / "workflows" / "update-and-deploy.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("scanner.py", workflow)
        self.assertIn('cron: "17 * * * *"', workflow)
        self.assertIn("update_watchlist.py --non-interactive", workflow)
        self.assertIn("update_portfolio.py --non-interactive", workflow)
        self.assertIn('CONFIG_FILE="${GITHUB_WORKSPACE}/.firebase-ci.json"', workflow)
        self.assertIn('cd "$GITHUB_WORKSPACE"', workflow)

    def test_two_tabs_and_four_digit_pin_ui(self) -> None:
        root = Path(__file__).resolve().parent.parent
        html = (root / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="watchlistTab"', html)
        self.assertIn('id="portfolioTab"', html)
        self.assertIn('id="watchlistView"', html)
        self.assertIn('id="portfolioView"', html)
        self.assertIn("Yield Monitor", html)
        self.assertIn('id="assetChart"', html)
        self.assertIn('id="realizedChart"', html)
        self.assertIn('id="yieldWeekSlider"', html)
        self.assertIn('id="yieldRangeLabel"', html)
        self.assertIn('id="monthlyChart"', html)
        self.assertIn('id="diagnostics"', html)
        self.assertIn('id="watchlistBody"', html)
        self.assertIn('id="technicalChartModal"', html)
        self.assertIn('id="technicalChartHost"', html)
        self.assertNotIn("저평가 구간에서", html)
        self.assertIn('pattern="[0-9]{4}"', html)
        self.assertIn('maxlength="4"', html)


    def test_mobile_tables_and_clickable_technical_chart_ui(self) -> None:
        root = Path(__file__).resolve().parent.parent
        css = (root / "docs" / "assets" / "styles.css").read_text(encoding="utf-8")
        js = (root / "docs" / "assets" / "app.mjs").read_text(encoding="utf-8")
        self.assertIn("#watchlistBody tr", css)
        self.assertIn("grid-template-columns: repeat(4", css)
        self.assertIn("openTechnicalChart", js)
        self.assertIn("chart_bars", js)
        self.assertIn("data-label=\"현재가\"", js)
        self.assertIn("data-label=\"평가금액\"", js)



if __name__ == "__main__":
    unittest.main()
