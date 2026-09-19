from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Iterable

from src.config import AUTH_BASE, INSTRUMENTS_BASE, ConnectionProfile
from src.models import Account, Holding
from src.portfolio import (
    merge_trades, normalize_daily_executions, normalize_total_transactions, number,
)


def _dict_or_first(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _sellable_qty(result: dict[str, Any]) -> float | None:
    preferred = (
        "sll_pbl_qty", "orr_pbl_qty", "max_pbl_qty", "bnc_qty",
        "sellable_qty", "sellableQuantity", "cns_bse_bnc_qty",
    )
    for key in preferred:
        if key in result and str(result.get(key, "")).strip() != "":
            return max(0.0, number(result.get(key)))
    for key, value in result.items():
        lowered = str(key).lower()
        if "qty" in lowered and any(token in lowered for token in ("sll", "sell", "pbl", "psbl", "bnc")):
            try:
                return max(0.0, float(str(value).replace(",", "").strip()))
            except ValueError:
                pass
    return None


def _date_digits(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8]


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _first_number(row: dict[str, Any], *keys: str) -> float:
    value = _first_value(row, *keys)
    return number(value)


def _us_realized_event(row: dict[str, Any], fallback_day: str = "") -> dict[str, Any] | None:
    """Normalize one overseas realized-P/L row from periodPnl or periodPnlDetail."""
    day = _date_digits(_first_value(row, "orr_dt", "sll_dt", "trd_dt", "trad_dt")) or fallback_day
    code = str(_first_value(row, "iem_cd", "pdno", "ovrs_pdno", "item_code") or "").strip().upper()
    if len(day) != 8 or not code:
        return None

    qty = abs(_first_number(row, "sll_qty", "sell_qty", "tot_sll_qty", "qty"))
    pnl = _first_number(
        row,
        "fc_rzt_pls", "rzt_pls", "frcr_rlzt_pfls_amt", "ovrs_rlzt_pfls_amt",
        "rlzt_pfls", "pls_amt",
    )
    sell_price = abs(_first_number(row, "sll_uit_pr", "sell_uit_pr", "sll_pr", "sell_price"))
    buy_price = abs(_first_number(row, "byn_uit_pr", "buy_uit_pr", "byn_pr", "buy_price"))

    # periodPnl is itself a realized-P/L endpoint, so a valid dated security row is an
    # event even when the realized amount is exactly zero. Detail endpoints can also
    # return zero-profit sales, hence code/date are the authoritative presence signal.
    return {
        "id": f"US:{day}:{code}",
        "date": day,
        "market": "US",
        "code": code,
        "name": str(_first_value(row, "iem_nm", "prdt_name", "ovrs_item_name", "item_name") or code).strip(),
        "qty": qty,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "realized_pnl_krw": pnl,
        "return_pct": _first_number(row, "fc_rzt_pft_rt", "rzt_pft_rt", "pft_rt", "return_pct"),
        "fee_krw": abs(_first_number(row, "fc_sdr_xps", "sdr_xps", "fee_sum", "fee")),
        "tax_krw": abs(_first_number(row, "tax_sum", "tax")),
        "currency": "KRW",
        "pnl_available": True,
        "source": "period_pnl",
    }


def _merge_realized_event(primary: dict[str, Any], supplement: dict[str, Any]) -> dict[str, Any]:
    """Keep periodPnl as source of truth while filling blanks from detail rows."""
    merged = dict(primary)
    for key in ("name", "qty", "buy_price", "sell_price", "return_pct", "fee_krw", "tax_krw"):
        current = merged.get(key)
        if current in (None, "", 0, 0.0) and supplement.get(key) not in (None, "", 0, 0.0):
            merged[key] = supplement[key]
    # Only replace P/L when the primary row did not supply it. Never turn an
    # unavailable transaction-ledger fallback into a fabricated zero-profit sale.
    if merged.get("realized_pnl_krw") in (None, "") and supplement.get("realized_pnl_krw") not in (None, ""):
        merged["realized_pnl_krw"] = supplement.get("realized_pnl_krw")
        merged["pnl_available"] = supplement.get("pnl_available", True)
    if merged.get("pnl_available") is not False and merged.get("realized_pnl_krw") not in (None, ""):
        merged["pnl_available"] = True
    if supplement.get("source") == "period_pnl_detail" and merged.get("source") != "period_pnl":
        merged["source"] = "period_pnl_detail"
    return merged




def _us_sell_fallback_events(trades: Iterable[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Build minimum US realized-sale records from the transaction ledger.

    These rows intentionally do not invent realized P/L. They are used only when
    periodPnl/periodPnlDetail fail to return a sale that is already visible in the
    account transaction history. Multiple fills of the same symbol on the same day
    are aggregated into one row to match the period-P/L table granularity.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for trade in trades or []:
        if not isinstance(trade, dict):
            continue
        if str(trade.get("market") or "").upper() != "US" or str(trade.get("side") or "").upper() != "SELL":
            continue
        day = _date_digits(trade.get("date"))
        code = str(trade.get("code") or "").strip().upper()
        qty = abs(number(trade.get("qty")))
        if len(day) != 8 or not code or qty <= 0:
            continue
        event_id = f"US:{day}:{code}"
        price = abs(number(trade.get("price")))
        existing = grouped.get(event_id)
        if existing is None:
            grouped[event_id] = {
                "id": event_id,
                "date": day,
                "market": "US",
                "code": code,
                "name": str(trade.get("name") or code).strip(),
                "qty": qty,
                "buy_price": None,
                "sell_price": price if price > 0 else None,
                "realized_pnl_krw": None,
                "return_pct": None,
                "fee_krw": None,
                "tax_krw": None,
                "currency": str(trade.get("currency") or "USD").upper(),
                "pnl_available": False,
                "source": "transaction_fallback",
            }
            continue
        old_qty = number(existing.get("qty"))
        new_qty = old_qty + qty
        old_price = number(existing.get("sell_price"))
        if price > 0 and new_qty > 0:
            existing["sell_price"] = ((old_price * old_qty) + (price * qty)) / new_qty
        existing["qty"] = new_qty
        if not existing.get("name") and trade.get("name"):
            existing["name"] = str(trade.get("name"))
    return grouped


def _cash_side(row: dict[str, Any]) -> str:
    code = str(row.get("act_trd_dtl_cd") or "").upper()
    text = " ".join(
        str(row.get(key) or "")
        for key in ("act_trd_tp_nm", "sps_cd_krl_anm", "trd_mdi_nm")
    )
    if code == "AA" or ("입금" in text and "출금" not in text):
        return "DEPOSIT"
    if code == "BB" or "출금" in text:
        return "WITHDRAW"
    return ""


def normalize_cash_flows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        side = _cash_side(row)
        if not side:
            continue
        currency = str(row.get("cur_cd") or "KRW").upper()
        amount = abs(number(row.get("xcl_amt") or row.get("trd_amt") or row.get("pbk_amt")))
        fx_rate = number(row.get("aly_xcg_rt"), 1.0 if currency == "KRW" else 0.0)
        amount_krw = amount if currency == "KRW" else amount * fx_rate
        trade_date = _date_digits(row.get("ral_trd_dt") or row.get("trd_dt"))
        serial = str(row.get("trd_sno") or row.get("fcl_sip_no") or index)
        event_id = f"FLOW:{trade_date}:{serial}:{currency}:{side}"
        normalized[event_id] = {
            "id": event_id,
            "date": trade_date,
            "time": str(row.get("rgs_tm") or ""),
            "side": side,
            "label": str(row.get("act_trd_tp_nm") or row.get("sps_cd_krl_anm") or side),
            "amount": amount,
            "amount_krw": amount_krw,
            "currency": currency,
        }
    return sorted(normalized.values(), key=lambda item: (item["date"], item["time"], item["id"]))


class NhReadOnlyClient:
    """Small read-only wrapper for account balances and execution history."""

    def __init__(self) -> None:
        self.profile: ConnectionProfile | None = None
        self._call = None
        self._api_lock = threading.Lock()
        self._next_api_at = 0.0
        self._rate_limit_until = 0.0
        self._min_api_interval = 0.40

    @property
    def environment(self) -> str:
        return self.profile.environment if self.profile else "disconnected"

    def configure(self, profile: ConnectionProfile) -> None:
        if not profile.app_key.strip() or not profile.app_secret.strip():
            raise ValueError("AppKey와 AppSecret이 필요합니다.")
        if profile.environment not in {"live", "mock"}:
            raise ValueError("환경은 live 또는 mock이어야 합니다.")
        self.profile = profile
        os.environ["NHPLUG_APP_KEY"] = profile.app_key.strip()
        os.environ["NHPLUG_APP_SECRET"] = profile.app_secret.strip()
        os.environ["NHPLUG_BASE_URL"] = profile.base_url
        os.environ["NHPLUG_AUTH_URL"] = AUTH_BASE
        os.environ["NHPLUG_INSTRUMENTS_BASE"] = INSTRUMENTS_BASE
        for name in list(sys.modules):
            if name == "nhplug" or name.startswith("nhplug."):
                del sys.modules[name]
        self._call = getattr(importlib.import_module("nhplug"), "call")

    def _api(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._call or not self.profile:
            raise RuntimeError("NHPLUG 연결 설정이 없습니다.")
        with self._api_lock:
            now = time.monotonic()
            target = max(self._next_api_at, self._rate_limit_until)
            if target > now:
                time.sleep(target - now)
            try:
                result = self._call(path, payload or {})
            except Exception as exc:
                message = str(exc)
                if "429" in message or "rate_limit" in message.lower():
                    self._rate_limit_until = time.monotonic() + 3.0
                raise
            finally:
                self._next_api_at = time.monotonic() + self._min_api_interval
        if not isinstance(result, dict):
            raise RuntimeError(f"NHPLUG 응답 형식 오류: {path}")
        return result

    def accounts(self) -> list[Account]:
        data = self._api("/n2/acctinfo", {})
        allowed = self.profile.required_account_types if self.profile else set()
        accounts = [
            Account(str(row.get("acct_no") or ""), str(row.get("acct_type") or ""))
            for row in _list(data.get("Output_0"))
        ]
        return [item for item in accounts if item.account_no and item.account_type in allowed]

    def domestic_balance(self, account_no: str) -> tuple[dict[str, Any], list[Holding]]:
        data = self._api("/krstock/inquiry/v1/balance", {
            "act_no": account_no,
            "bnc_bse_cd": "5",
            "ltg_aot_dit_cd": "1",
            "aet_bse": "1",
            "qut_dit_cd": "UNT",
            "aly_qut_cd": "1",
        })
        summary = _dict_or_first(data.get("Output_0"))
        holdings = [
            Holding(
                market="KR",
                code=str(row.get("iem_cd", "")).zfill(6),
                name=str(row.get("iem_nm", "")),
                qty=number(row.get("itg_bnc_qty")),
                avg_price=number(row.get("phs_pr")),
                current_price=number(row.get("now_pr")),
                eval_amount_krw=number(row.get("eal_amt")),
                pnl_amount_krw=number(row.get("eal_pls_amt")),
                pnl_pct=number(row.get("pft_rt")),
                price_currency="KRW",
            )
            for row in _list(data.get("Output_1"))
            if number(row.get("itg_bnc_qty")) != 0
        ]
        return summary, holdings

    def overseas_balance(self, account_no: str) -> tuple[dict[str, Any], list[Holding]]:
        data = self._api("/gbstock/inquiry/v1/balance", {
            "act_no": account_no,
            "qut_iqr_dit_cd": "9",
            "fc_sec_trd_nat_cd": "200",
            "cur_cd": "KRW",
            "xns_dit_cd": "1",
        })
        summary = _dict_or_first(data.get("Output_0"))
        holdings = [
            Holding(
                market="US",
                code=str(row.get("iem_cd", "")).upper(),
                name=str(row.get("iem_nm") or row.get("oss_iem_eng_nm") or ""),
                qty=number(row.get("cns_bse_bnc_qty")),
                avg_price=number(row.get("fc_avg_phs_pr") or row.get("fc_phs_uit_pr")),
                current_price=number(row.get("fc_sec_end_pr")),
                eval_amount_krw=number(row.get("krw_eal_amt") or row.get("fc_eal_amt")),
                pnl_amount_krw=number(row.get("krw_eal_pls_amt") or row.get("fc_eal_pls_amt")),
                pnl_pct=number(row.get("eal_pft_rt")),
                price_currency="USD",
            )
            for row in _list(data.get("Output_1"))
            if number(row.get("cns_bse_bnc_qty")) != 0
        ]
        return summary, holdings

    def sellable_quantity(self, account_no: str, holding: Holding) -> float | None:
        if holding.market == "KR":
            data = self._api("/krstock/inquiry/v1/sellableQuantity", {
                "act_no": account_no,
                "iem_cd": holding.code.zfill(6),
                "cfd_lon_cd": "00",
            })
            return _sellable_qty(_dict_or_first(data.get("Output_0")))
        data = self._api("/gbstock/inquiry/v1/buyableAmount", {
            "act_no": account_no,
            "pcs_dit": "3",
            "fc_sec_trd_nat_cd": "200",
            "iem_cd": holding.code.upper(),
            "wtm_cur_knd_cd": "2",
            "oss_orr_knd_cd": "1",
            "ahi_nmn_pr_tp_cd": "03",
        })
        return _sellable_qty(_dict_or_first(data.get("Output_0")))

    def all_holdings(self, account_no: str) -> tuple[list[Holding], list[str]]:
        holdings: list[Holding] = []
        warnings: list[str] = []
        try:
            _, domestic = self.domestic_balance(account_no)
            holdings.extend(domestic)
        except Exception as exc:
            warnings.append(f"국내 잔고 조회 실패: {exc}")
        try:
            _, overseas = self.overseas_balance(account_no)
            holdings.extend(overseas)
        except Exception as exc:
            warnings.append(f"미국 잔고 조회 실패: {exc}")

        filtered: list[Holding] = []
        for index, holding in enumerate(holdings):
            try:
                qty = self.sellable_quantity(account_no, holding)
                if qty is None:
                    filtered.append(holding)
                    continue
                holding.sellable_qty = qty
                holding.position_verified = True
                if qty > 1e-12:
                    filtered.append(holding)
            except Exception as exc:
                text = str(exc)
                warnings.append(f"{holding.market} {holding.code} 보유수량 검증 실패: {text}")
                filtered.append(holding)
                if "429" in text or "rate_limit" in text.lower():
                    filtered.extend(holdings[index + 1:])
                    break
        return filtered, warnings

    def account_asset_status(self, account_no: str) -> dict[str, Any]:
        """Integrated KRW account values, including cash and foreign positions."""
        data = self._api("/krstock/inquiry/v1/assetStatus", {
            "act_no": account_no,
            "eal_aly_cd": "2",
            "aet_bse": "1",
            "qut_dit_cd": "UNT",
            "aly_qut_cd": "1",
        })
        row = _dict_or_first(data.get("Output_0"))
        return {
            "total_asset_krw": number(row.get("tot_aet_amt")),
            "net_asset_krw": number(row.get("nas_amt")),
            "cash_krw": number(row.get("dca")),
            "evaluation_krw": number(row.get("tot_eal_amt")),
            "unrealized_pnl_krw": number(row.get("tot_eal_pls_amt")),
            "unrealized_pnl_pct": number(row.get("pft_rt")),
        }

    def cash_flow_history(
        self, account_no: str, start_date: datetime, end_date: datetime,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if self.environment != "live":
            return [], ["모의투자는 입출금내역 API를 제공하지 않습니다."]
        try:
            data = self._api("/common/inquiry/v1/depositWithdrawal", {
                "iqr_tp_cd": "1",
                "act_no": account_no,
                "iqr_sta_dt": start_date.strftime("%Y%m%d"),
                "iqr_end_dt": end_date.strftime("%Y%m%d"),
                "act_trd_dtl_cd": "01",
            })
            return normalize_cash_flows(_list(data.get("Output_0"))), []
        except Exception as exc:
            return [], [f"입출금내역 조회 실패: {exc}"]

    def realized_pnl_history(
        self, account_no: str, start_date: datetime, end_date: datetime,
        trades: Iterable[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        events: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        diagnostics: dict[str, Any] = {
            "kr_events": 0,
            "us_period_rows": 0,
            "us_period_events": 0,
            "us_detail_calls": 0,
            "us_detail_rows": 0,
            "us_sell_trades": 0,
            "us_sell_days": 0,
            "us_transaction_fallback_events": 0,
            "us_pnl_unavailable_events": 0,
            "us_period_rsp_cd": "",
            "us_period_rsp_msg": "",
            "us_events": 0,
        }
        transaction_fallback = _us_sell_fallback_events(trades)
        diagnostics["us_sell_trades"] = sum(
            1 for item in (trades or [])
            if isinstance(item, dict)
            and str(item.get("market") or "").upper() == "US"
            and str(item.get("side") or "").upper() == "SELL"
        )
        diagnostics["us_sell_days"] = len({item["date"] for item in transaction_fallback.values()})
        cursor = start_date
        while cursor.date() <= end_date.date():
            if cursor.weekday() < 5:
                day = cursor.strftime("%Y%m%d")
                try:
                    data = self._api("/krstock/inquiry/v1/tradingPnl", {
                        "act_no": account_no,
                        "iqr_sta_dt": day,
                        "iqr_end_dt": day,
                    })
                    for row in _list(data.get("Output_1")):
                        code = str(row.get("iem_cd") or "").strip().zfill(6)
                        sell_qty = number(row.get("sll_qty"))
                        if not code or sell_qty <= 0:
                            continue
                        event_id = f"KR:{day}:{code}"
                        events[event_id] = {
                            "id": event_id,
                            "date": day,
                            "market": "KR",
                            "code": code,
                            "name": str(row.get("iem_nm") or code).strip(),
                            "qty": sell_qty,
                            "buy_price": number(row.get("byn_uit_pr")),
                            "sell_price": number(row.get("sll_uit_pr")),
                            "realized_pnl_krw": number(row.get("pls_amt")),
                            "return_pct": number(row.get("pft_rt")),
                            "fee_krw": number(row.get("fee_sum")),
                            "tax_krw": number(row.get("tax_sum")),
                            "currency": "KRW",
                        }
                except Exception as exc:
                    warnings.append(f"{day} 국내 실현손익 조회 실패: {exc}")
            cursor += timedelta(days=1)

        diagnostics["kr_events"] = sum(1 for item in events.values() if item.get("market") == "KR")

        try:
            period = self._api("/gbstock/inquiry/v1/periodPnl", {
                "act_no": account_no,
                "iqr_dit": "2",
                "sta_orr_dt": start_date.strftime("%Y%m%d"),
                "end_orr_dt": end_date.strftime("%Y%m%d"),
                "iem_cd": "",
                "trd_cur_cd": "KRW",
                "fc_sec_trd_nat_cd": "200",
            })
            period_rows = _list(period.get("Output_1"))
            diagnostics["us_period_rows"] = len(period_rows)
            diagnostics["us_period_rsp_cd"] = str(period.get("rsp_cd") or "")
            diagnostics["us_period_rsp_msg"] = str(period.get("rsp_msg") or "")

            # Primary source: periodPnl.Output_1. The transaction ledger supplies an
            # independent set of US SELL dates so periodPnlDetail can still be tried
            # when periodPnl unexpectedly returns an empty first block.
            sale_dates: set[str] = {item["date"] for item in transaction_fallback.values()}
            for row in period_rows:
                event = _us_realized_event(row)
                if not event:
                    continue
                sale_dates.add(str(event["date"]))
                events[event["id"]] = event
            diagnostics["us_period_events"] = sum(
                1 for item in events.values() if item.get("market") == "US"
            )

            if not period_rows and transaction_fallback:
                message = diagnostics["us_period_rsp_msg"]
                suffix = f" (API 메시지: {message})" if message else ""
                warnings.append(
                    "미국 기간손익 periodPnl은 0건이지만 최근 거래내역에서 "
                    f"미국 매도 {len(transaction_fallback)}종목을 확인해 상세조회로 보완합니다.{suffix}"
                )
            elif not period_rows and not transaction_fallback:
                message = diagnostics["us_period_rsp_msg"]
                suffix = f" (API 메시지: {message})" if message else ""
                warnings.append(
                    "미국 기간손익 periodPnl이 0건이고 최근 종합거래내역에서도 미국 SELL을 "
                    f"찾지 못했습니다. 실제 미국 매도가 있었다면 거래내역 조회조건을 확인하세요.{suffix}"
                )
            elif diagnostics["us_period_events"] == 0:
                warnings.append(
                    f"미국 기간손익 Output_1 {len(period_rows)}건을 받았지만 종목·매도일을 "
                    "실현 이벤트로 해석하지 못했습니다. 응답 필드 형식을 확인하세요."
                )

            for day in sorted(sale_dates):
                try:
                    diagnostics["us_detail_calls"] += 1
                    detail = self._api("/gbstock/inquiry/v1/periodPnlDetail", {
                        "act_no": account_no,
                        "iqr_dit": "2",
                        "iem_cd": "",
                        "orr_dt": day,
                        "fc_sec_trd_nat_cd": "200",
                        "trd_cur_cd": "KRW",
                    })
                    detail_rows = _list(detail.get("Output_0"))
                    diagnostics["us_detail_rows"] += len(detail_rows)
                    for row in detail_rows:
                        event = _us_realized_event(row, day)
                        if not event:
                            continue
                        event["source"] = "period_pnl_detail"
                        event["pnl_available"] = True
                        if event["id"] in events:
                            events[event["id"]] = _merge_realized_event(events[event["id"]], event)
                        else:
                            events[event["id"]] = event
                except Exception as exc:
                    # Detail lookup is non-fatal because either periodPnl or the transaction
                    # ledger can still preserve the fact that a US sale occurred.
                    warnings.append(f"{day} 미국 실현손익 상세조회 실패(확인된 매도내역은 유지): {exc}")

            # Final safety net: every US SELL visible in transaction history must remain
            # visible in the portfolio even when both P/L endpoints return no row.
            for event_id, fallback_event in transaction_fallback.items():
                if event_id in events:
                    events[event_id] = _merge_realized_event(events[event_id], fallback_event)
                else:
                    events[event_id] = fallback_event
                    diagnostics["us_transaction_fallback_events"] += 1
            diagnostics["us_pnl_unavailable_events"] = sum(
                1 for item in events.values()
                if item.get("market") == "US" and item.get("pnl_available") is False
            )
            if diagnostics["us_pnl_unavailable_events"]:
                warnings.append(
                    f"미국 매도 {diagnostics['us_pnl_unavailable_events']}종목은 체결내역은 확인했지만 "
                    "기간손익/상세손익이 없어 종목·수량·매도가는 표시하고 실현손익은 '—'로 표시합니다."
                )
        except Exception as exc:
            warnings.append(f"미국주식 실현손익 조회 실패: {exc}")
            for event_id, fallback_event in transaction_fallback.items():
                if event_id not in events:
                    events[event_id] = fallback_event
                    diagnostics["us_transaction_fallback_events"] += 1
            diagnostics["us_pnl_unavailable_events"] = sum(
                1 for item in events.values()
                if item.get("market") == "US" and item.get("pnl_available") is False
            )
            if diagnostics["us_pnl_unavailable_events"]:
                warnings.append(
                    f"미국 매도 {diagnostics['us_pnl_unavailable_events']}종목은 거래내역으로 보존했지만 "
                    "실현손익 API 조회에 실패해 손익은 '—'로 표시합니다."
                )
        diagnostics["us_events"] = sum(1 for item in events.values() if item.get("market") == "US")
        return (
            sorted(events.values(), key=lambda item: (item["date"], item["market"], item["code"])),
            warnings,
            diagnostics,
        )

    def market_bars(self, market: str, code: str, count: int = 260) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Fetch read-only live quote metadata and ascending daily OHLC bars."""
        market = market.upper()
        today = datetime.now().strftime("%Y%m%d")
        if market == "KR":
            data = self._api("/krstock/quote/v1/period", {
                "market_cd": "UNT",
                "iem_cd": code.zfill(6),
                "mrkt_div_cls_code": "",
                "edate": today,
                "array_cnt": str(count),
                "maxavg": "200",
                "gubun": "1",
                "xtick": "001",
                "today_cls_code": "0",
                "fake_tick": "1",
                "sur_flag": "0",
                "sur_gb_day_cnt": "00",
                "sur_bf_end_time": "",
                "out1_scale_change": "0",
                "out2_scale_change": "0",
                "view_main_yn": "Y",
            })
            metadata = _dict_or_first(data.get("Output_0"))
            bars = [
                {
                    "date": _date_digits(row.get("bsop_date")),
                    "open": number(row.get("stck_oprc")),
                    "high": number(row.get("stck_hgpr")),
                    "low": number(row.get("stck_lwpr")),
                    "close": number(row.get("stck_prpr")),
                    "volume": number(row.get("vol")),
                }
                for row in _list(data.get("Output_1"))
            ]
            return metadata, sorted(bars, key=lambda row: row["date"])

        period = self._api("/gbstock/quote/v1/period", {
            "iem_cd": code.upper(),
            "end_dt": today,
            "count": str(count),
            "maxavg": "200",
            "gubun": "3",
            "xtick": "0001",
            "today_cls": "0",
            "market_cls": "1",
        })
        current = self._api("/gbstock/quote/v1/current", {"iem_cd": code.upper()})
        metadata = {
            **_dict_or_first(period.get("Output_0")),
            **_dict_or_first(current.get("Output_0")),
        }
        bars = [
            {
                "date": _date_digits(row.get("trade_date") or row.get("bsop_date")),
                "open": number(row.get("open_prc")),
                "high": number(row.get("high")),
                "low": number(row.get("low")),
                "close": number(row.get("close_prc")),
                "volume": number(row.get("movolume")),
            }
            for row in _list(period.get("Output_1"))
        ]
        return metadata, sorted(bars, key=lambda row: row["date"])

    def transaction_history(
        self, account_no: str, start_date: datetime, end_date: datetime,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        start_text = start_date.strftime("%Y%m%d")
        end_text = end_date.strftime("%Y%m%d")
        warnings: list[str] = []
        if self.environment == "live":
            try:
                data = self._api("/common/inquiry/v1/totalTransaction", {
                    "iqr_tp_cd": "2",
                    "iqr_rge_cd": "1",
                    "act_no": account_no,
                    "iqr_sta_dt": start_text,
                    "iqr_end_dt": end_text,
                    "iem_llf_cd": "00",
                    "act_trd_dtl_cd": "03",
                })
                return normalize_total_transactions(_list(data.get("Output_0"))), warnings
            except Exception as exc:
                warnings.append(f"종합거래내역 조회 실패로 일별 체결조회를 사용합니다: {exc}")

        domestic: list[dict[str, Any]] = []
        overseas: list[dict[str, Any]] = []
        cursor = start_date
        while cursor.date() <= end_date.date():
            if cursor.weekday() < 5:
                day = cursor.strftime("%Y%m%d")
                try:
                    data = self._api("/krstock/inquiry/v1/dailyOrderExecution", {
                        "orr_dt": day,
                        "act_no": account_no,
                        "orr_mkt_cd": "00",
                        "ost_cns_dit": "1",
                    })
                    domestic.extend(normalize_daily_executions(_list(data.get("Output_1")), "KR", day))
                except Exception as exc:
                    warnings.append(f"{day} 국내 체결조회 실패: {exc}")
                try:
                    data = self._api("/gbstock/inquiry/v1/unexecuted", {
                        "orr_dt": day,
                        "act_no": account_no,
                        "oss_sby_dit_cd": "0",
                        "sot_dit": "1",
                        "ost_cns_dit": "1",
                    })
                    overseas.extend(normalize_daily_executions(_list(data.get("Output_0")), "US", day))
                except Exception as exc:
                    warnings.append(f"{day} 미국 체결조회 실패: {exc}")
            cursor += timedelta(days=1)
        return merge_trades(domestic, overseas), warnings
