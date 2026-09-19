from __future__ import annotations

import unittest
from datetime import date, datetime
from pathlib import Path

from src.market_indicators import bollinger_state, technical_snapshot, watchlist_sort_key
from src.models import Holding
from src.nh_client import NhReadOnlyClient, normalize_cash_flows
from src.portfolio import (
    backfill_daily_realized_history,
    decrypt_envelope,
    encrypt_payload,
    holdings_for_web,
    mask_account,
    merge_persistent_events,
    monthly_realized_performance,
    normalize_total_transactions,
    portfolio_totals,
    realized_summary,
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

    def test_us_period_pnl_is_primary_when_detail_is_empty(self) -> None:
        class FakeClient(NhReadOnlyClient):
            @property
            def environment(self) -> str:
                return "live"

            def _api(self, path: str, payload=None):
                if path.endswith("/tradingPnl"):
                    return {"Output_1": []}
                if path.endswith("/periodPnl"):
                    return {"Output_1": [{
                        "orr_dt": "20260918", "iem_cd": "AAPL", "iem_nm": "Apple",
                        "sll_qty": "2", "byn_uit_pr": "200", "sll_uit_pr": "210",
                        "fc_rzt_pls": "28000", "fc_rzt_pft_rt": "5.0",
                    }]}
                if path.endswith("/periodPnlDetail"):
                    return {"Output_0": []}
                raise AssertionError(path)

        events, warnings, diagnostics = FakeClient().realized_pnl_history(
            "123", datetime(2026, 9, 18), datetime(2026, 9, 18)
        )
        us = [item for item in events if item["market"] == "US"]
        self.assertEqual(len(us), 1)
        self.assertEqual(us[0]["code"], "AAPL")
        self.assertEqual(us[0]["realized_pnl_krw"], 28000)
        self.assertEqual(diagnostics["us_period_rows"], 1)
        self.assertEqual(diagnostics["us_events"], 1)
        self.assertFalse(any("미국주식 실현손익 조회 실패" in warning for warning in warnings))

    def test_account_mask(self) -> None:
        self.assertEqual(mask_account("123-45-678901"), "***-***-8901")

    def test_no_trading_ui_or_execution_code(self) -> None:
        root = Path(__file__).resolve().parent.parent
        forbidden = ("PyQt6", "cashBuy", "cashSell", "place_order", "auto_trading")
        for path in list((root / "src").rglob("*.py")) + list((root / "docs").rglob("*.mjs")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{token} remains in {path}")

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

    def test_two_tabs_and_four_digit_pin_ui(self) -> None:
        root = Path(__file__).resolve().parent.parent
        html = (root / "docs" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="watchlistTab"', html)
        self.assertIn('id="portfolioTab"', html)
        self.assertIn('id="watchlistView"', html)
        self.assertIn('id="portfolioView"', html)
        self.assertIn("Yield Monitor", html)
        self.assertIn('id="yieldChart"', html)
        self.assertIn('id="monthlyChart"', html)
        self.assertIn('id="diagnostics"', html)
        self.assertIn('id="watchlistBody"', html)
        self.assertIn('pattern="[0-9]{4}"', html)
        self.assertIn('maxlength="4"', html)


if __name__ == "__main__":
    unittest.main()
