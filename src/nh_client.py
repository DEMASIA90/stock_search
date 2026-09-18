from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Any

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
    ) -> tuple[list[dict[str, Any]], list[str]]:
        events: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
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
            sale_dates = {
                _date_digits(row.get("orr_dt"))
                for row in _list(period.get("Output_1"))
                if number(row.get("sll_qty")) > 0 and _date_digits(row.get("orr_dt"))
            }
            for day in sorted(sale_dates):
                detail = self._api("/gbstock/inquiry/v1/periodPnlDetail", {
                    "act_no": account_no,
                    "iqr_dit": "2",
                    "iem_cd": "",
                    "orr_dt": day,
                    "fc_sec_trd_nat_cd": "200",
                    "trd_cur_cd": "KRW",
                })
                for row in _list(detail.get("Output_0")):
                    code = str(row.get("iem_cd") or "").strip().upper()
                    sell_qty = number(row.get("sll_qty"))
                    if not code or sell_qty <= 0:
                        continue
                    event_id = f"US:{day}:{code}"
                    events[event_id] = {
                        "id": event_id,
                        "date": day,
                        "market": "US",
                        "code": code,
                        "name": str(row.get("iem_nm") or code).strip(),
                        "qty": sell_qty,
                        "buy_price": number(row.get("byn_uit_pr")),
                        "sell_price": number(row.get("sll_uit_pr")),
                        "realized_pnl_krw": number(row.get("fc_rzt_pls")),
                        "return_pct": number(row.get("fc_rzt_pft_rt")),
                        "fee_krw": number(row.get("fc_sdr_xps")),
                        "tax_krw": 0.0,
                        "currency": "KRW",
                    }
        except Exception as exc:
            warnings.append(f"미국주식 실현손익 조회 실패: {exc}")
        return sorted(events.values(), key=lambda item: (item["date"], item["market"], item["code"])), warnings

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
