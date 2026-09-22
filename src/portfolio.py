from __future__ import annotations

import base64
import json
import os
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.config import ENVELOPE_AAD, PBKDF2_ITERATIONS
from src.models import Holding


SEOUL_TZ = ZoneInfo("Asia/Seoul")


def _seoul_datetime(value: datetime | None = None) -> datetime:
    current = value or datetime.now(SEOUL_TZ)
    if current.tzinfo is None:
        return current.replace(tzinfo=SEOUL_TZ)
    return current.astimezone(SEOUL_TZ)


def _next_calendar_day_key(value: Any) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())[:8]
    if len(raw) != 8:
        return raw
    try:
        return (datetime.strptime(raw, "%Y%m%d").date() + timedelta(days=1)).strftime("%Y%m%d")
    except ValueError:
        return raw


def realized_account_date(event: dict[str, Any]) -> str:
    explicit = "".join(ch for ch in str(event.get("account_date") or "") if ch.isdigit())[:8]
    if len(explicit) == 8:
        return explicit
    trade_day = "".join(ch for ch in str(event.get("date") or "") if ch.isdigit())[:8]
    if len(trade_day) != 8:
        return trade_day
    if str(event.get("market") or "").upper() == "US":
        # dailyTransaction fallback is already the observed Korea-account date.
        # periodPnlDetail is keyed by the U.S. exchange trade date and needs the
        # following Korea calendar day when no matched fallback supplied one.
        if event.get("source") == "transaction_fallback":
            return trade_day
        return _next_calendar_day_key(trade_day)
    return trade_day


def number(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def mask_account(account_no: str) -> str:
    digits = "".join(ch for ch in str(account_no) if ch.isdigit())
    return f"***-***-{digits[-4:]}" if digits else "계좌 미설정"


def trade_side(row: dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(key, "") or "")
        for key in (
            "act_trd_tp_nm", "sps_cd_krl_anm", "sps_cd_nm", "sby_dit_cd_nm",
            "sby_dit_nm", "oss_sby_dit_cd",
        )
    ).lower()
    if "매수" in text or "buy" in text or str(row.get("oss_sby_dit_cd", "")) == "2":
        return "BUY"
    if "매도" in text or "sell" in text or str(row.get("oss_sby_dit_cd", "")) == "1":
        return "SELL"
    return ""


def _trade_market(row: dict[str, Any]) -> str:
    category = str(row.get("iem_llf_cd", "") or "")
    currency = str(row.get("cur_cd", "") or "").upper()
    code = str(row.get("iem_cd", "") or "").strip()
    if category == "15" or (currency and currency != "KRW") or not code.isdigit():
        return "US"
    return "KR"


def normalize_total_transactions(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        side = trade_side(row)
        qty = abs(number(row.get("trd_qty")))
        if not side or qty <= 0:
            continue
        market = _trade_market(row)
        currency = str(row.get("cur_cd") or ("USD" if market == "US" else "KRW")).upper()
        price = abs(number(row.get("trd_uit_pr")))
        amount = abs(number(row.get("trd_amt"))) or qty * price
        fx_rate = number(row.get("aly_xcg_rt"))
        amount_krw = amount if currency == "KRW" else (amount * fx_rate if fx_rate > 0 else None)
        trade_date = str(row.get("ral_trd_dt") or row.get("trd_dt") or "")
        serial = str(row.get("trd_sno") or row.get("fcl_sip_no") or len(out))
        out.append({
            "id": f"COMMON:{trade_date}:{serial}:{market}:{row.get('iem_cd', '')}",
            "date": trade_date,
            "time": str(row.get("rgs_tm") or ""),
            "market": market,
            "code": str(row.get("iem_cd") or "").strip().upper(),
            "name": str(row.get("iem_nm") or "").strip(),
            "side": side,
            "qty": qty,
            "price": price,
            "amount": amount,
            "amount_krw": amount_krw,
            "currency": currency,
            "fee": abs(number(row.get("trd_orn_fee") or row.get("fc_fee"))),
            "tax": abs(number(row.get("tax_sum") or row.get("trd_tax"))),
        })
    return merge_trades(out)


def normalize_overseas_daily_transactions(
    rows: Iterable[dict[str, Any]], forced_side: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize official overseas dailyTransaction rows into the common ledger.

    ``forced_side`` is used when the endpoint itself was queried with an explicit
    buy/sell filter (05/06).  This keeps a valid execution even when a response
    omits the Korean transaction-type label used by ``trade_side``.
    """
    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        side = (forced_side or trade_side(row)).upper()
        if side not in {"BUY", "SELL"}:
            side = ""
        qty = abs(number(row.get("trd_qty")))
        trade_date = "".join(ch for ch in str(row.get("ral_trd_dt") or row.get("trd_dt") or "") if ch.isdigit())[:8]
        code = str(row.get("oss_iem_cd") or row.get("iem_cd") or "").strip().upper()
        if not side or qty <= 0 or len(trade_date) != 8 or not code:
            continue
        price = abs(number(row.get("trd_uit_pr")))
        amount = abs(number(row.get("fc_trd_amt") or row.get("fc_amt"))) or qty * price
        raw_krw = row.get("krw_trd_amt") or row.get("krw_amt")
        amount_krw = abs(number(raw_krw)) if raw_krw not in (None, "") else None
        fx_rate = number(row.get("aly_xcg_rt"))
        if amount_krw is None and fx_rate > 0:
            amount_krw = amount * fx_rate
        serial = str(row.get("trd_sno") or index)
        out.append({
            "id": f"USDAILY:{trade_date}:{serial}:{code}:{side}",
            "date": trade_date,
            "time": str(row.get("rgs_tm") or ""),
            "market": "US",
            "code": code,
            "name": str(row.get("iem_krl_nm") or row.get("oss_iem_nm") or code).strip(),
            "side": side,
            "qty": qty,
            "price": price,
            "amount": amount,
            "amount_krw": amount_krw,
            "currency": "USD",
            "fee": abs(number(row.get("ose_fee"))) + abs(number(row.get("dmt_fee"))),
            "tax": abs(number(row.get("fc_tax_sum") or row.get("tax_sum"))),
            "source": "gbstock_daily_transaction",
        })
    return merge_trades(out)


def normalize_daily_executions(
    rows: Iterable[dict[str, Any]], market: str, trade_date: str
) -> list[dict[str, Any]]:
    market = market.upper()
    out: list[dict[str, Any]] = []
    for row in rows:
        side = trade_side(row)
        qty_key = "tot_cns_qty" if market == "KR" else "cns_qty"
        price_key = "cns_avg_uit_pr" if market == "KR" else "cns_pr"
        qty = abs(number(row.get(qty_key)))
        if not side or qty <= 0:
            continue
        price = abs(number(row.get(price_key)))
        amount = abs(number(row.get("cns_amt"))) or qty * price
        currency = "KRW" if market == "KR" else "USD"
        order_no = str(row.get("itg_orr_no") or row.get("orr_no") or len(out))
        out.append({
            "id": f"DAILY:{trade_date}:{market}:{order_no}:{row.get('iem_cd', '')}",
            "date": str(row.get("orr_dt") or trade_date),
            "time": str(row.get("orr_tm") or row.get("rgs_tm") or ""),
            "market": market,
            "code": str(row.get("iem_cd") or "").strip().upper(),
            "name": str(row.get("iem_nm") or "").strip(),
            "side": side,
            "qty": qty,
            "price": price,
            "amount": amount,
            "amount_krw": amount if market == "KR" else None,
            "currency": currency,
            "fee": 0.0,
            "tax": 0.0,
        })
    return merge_trades(out)


def merge_trades(*groups: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for trade in group:
            merged[str(trade.get("id") or len(merged))] = dict(trade)
    return sorted(
        merged.values(),
        key=lambda item: (str(item.get("date", "")), str(item.get("time", "")), str(item.get("id", ""))),
        reverse=True,
    )


def holdings_for_web(holdings: Iterable[Holding]) -> list[dict[str, Any]]:
    return sorted(
        [asdict(item) for item in holdings],
        key=lambda item: number(item.get("eval_amount_krw")),
        reverse=True,
    )


def combine_account_totals(
    totals: dict[str, Any], asset_status: dict[str, Any],
) -> dict[str, Any]:
    """Overlay integrated account assets and guarantee deposit cash is included.

    NH assetStatus ``tot_aet_amt`` is authoritative. If it is unavailable,
    the fallback is evaluated securities plus ``dca`` (deposit cash / 예수금).
    """
    out = dict(totals)
    asset_total = number(asset_status.get("total_asset_krw"))
    asset_cash = number(asset_status.get("cash_krw"))
    asset_evaluation = number(asset_status.get("evaluation_krw"))
    for key, value in asset_status.items():
        if key != "total_asset_krw" and value is not None:
            out[key] = value
    if asset_total > 0:
        out["total_asset_krw"] = asset_total
    elif asset_evaluation or asset_cash:
        out["total_asset_krw"] = asset_evaluation + asset_cash
    else:
        out["total_asset_krw"] = number(out.get("evaluation_krw"))
    out["cash_krw"] = asset_cash
    out.setdefault("unrealized_pnl_krw", out.get("pnl_krw", 0))
    return out


def portfolio_totals(holdings: Iterable[dict[str, Any]], trades: Iterable[dict[str, Any]]) -> dict[str, Any]:
    positions = list(holdings)
    executions = list(trades)
    evaluation = sum(number(item.get("eval_amount_krw")) for item in positions)
    pnl = sum(number(item.get("pnl_amount_krw")) for item in positions)
    principal = evaluation - pnl

    def trade_sum(side: str, currency: str) -> float:
        return sum(
            number(item.get("amount"))
            for item in executions
            if item.get("side") == side and item.get("currency") == currency
        )

    return {
        "evaluation_krw": evaluation,
        "principal_krw": principal,
        "pnl_krw": pnl,
        "pnl_pct": pnl / principal * 100.0 if principal else 0.0,
        "kr_krw": sum(number(item.get("eval_amount_krw")) for item in positions if item.get("market") == "KR"),
        "us_krw": sum(number(item.get("eval_amount_krw")) for item in positions if item.get("market") == "US"),
        "holding_count": len(positions),
        "buy_krw": trade_sum("BUY", "KRW"),
        "sell_krw": trade_sum("SELL", "KRW"),
        "buy_usd": trade_sum("BUY", "USD"),
        "sell_usd": trade_sum("SELL", "USD"),
    }


def update_snapshot_history(
    history: Iterable[dict[str, Any]], totals: dict[str, Any], at: datetime | None = None,
    limit: int = 43_800,
) -> list[dict[str, Any]]:
    now = _seoul_datetime(at)
    stamp = now.isoformat(timespec="minutes")
    point = {
        "at": stamp,
        "snapshot_date": now.strftime("%Y%m%d"),
        "evaluation_krw": number(totals.get("evaluation_krw")),
        "pnl_krw": number(totals.get("pnl_krw")),
        "kr_krw": number(totals.get("kr_krw")),
        "us_krw": number(totals.get("us_krw")),
    }
    out = [dict(item) for item in history if isinstance(item, dict) and item.get("at")]
    if out and str(out[-1].get("at", ""))[:16] == stamp[:16]:
        out[-1] = point
    else:
        out.append(point)
    return out[-max(1, limit):]


def merge_persistent_events(
    previous: Iterable[dict[str, Any]], recent: Iterable[dict[str, Any]], *, limit: int = 20_000,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for event in (*list(previous), *list(recent)):
        if not isinstance(event, dict) or not event.get("id"):
            continue
        merged[str(event["id"])] = dict(event)
    ordered = sorted(
        merged.values(),
        key=lambda item: (str(item.get("date", "")), str(item.get("time", "")), str(item.get("id", ""))),
    )
    return ordered[-max(1, limit):]


def sanitize_legacy_realized_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair legacy U.S. fallback rows that fabricated a zero realized P/L.

    A ``transaction_fallback`` row proves that a SELL execution existed, but it
    never proves realized P/L. Older builds stored zero in that case. Convert
    those rows back to an explicit unknown value so the UI shows ``—`` and the
    value is excluded from cumulative realized-P/L statistics.
    """
    repaired: list[dict[str, Any]] = []
    for item in events:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if str(row.get("market") or "").upper() == "US" and row.get("source") == "transaction_fallback":
            row["realized_pnl_krw"] = None
            row["return_pct"] = None
            row["pnl_available"] = False
        repaired.append(row)
    return repaired



def _us_code_aliases(value: Any) -> set[str]:
    """Return conservative aliases for an overseas security code.

    NH overseas endpoints can expose the same security through different code
    fields (for example the overseas symbol versus an internal item code).
    Aliases are used only to reconcile an already-confirmed P/L row with an
    already-confirmed SELL ledger row; they are never used to invent trades.
    """
    raw = str(value or "").strip().upper()
    if not raw:
        return set()
    aliases = {"".join(ch for ch in raw if ch.isalnum())}
    for separator in (".", ":", "/", "-"):
        if separator in raw:
            parts = [part.strip() for part in raw.split(separator) if part.strip()]
            for part in parts:
                cleaned = "".join(ch for ch in part if ch.isalnum())
                if cleaned:
                    aliases.add(cleaned)
    return {item for item in aliases if item}


def _us_name_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _date_distance(left: Any, right: Any) -> int | None:
    left_text = "".join(ch for ch in str(left or "") if ch.isdigit())[:8]
    right_text = "".join(ch for ch in str(right or "") if ch.isdigit())[:8]
    if len(left_text) != 8 or len(right_text) != 8:
        return None
    try:
        left_day = datetime.strptime(left_text, "%Y%m%d").date()
        right_day = datetime.strptime(right_text, "%Y%m%d").date()
    except ValueError:
        return None
    return abs((left_day - right_day).days)


def _us_sale_match_score(fallback: dict[str, Any], resolved: dict[str, Any]) -> float | None:
    """Score whether two rows represent the same U.S. sale.

    dailyTransaction and periodPnlDetail can use slightly different code/date
    representations (notably around the U.S./Korea trading-date boundary).  We
    reconcile only within four calendar days and require a security identity
    signal (code/name), or an otherwise unique quantity+price signature.
    """
    distance = _date_distance(fallback.get("date"), resolved.get("date"))
    if distance is None or distance > 4:
        return None

    code_match = bool(_us_code_aliases(fallback.get("code")) & _us_code_aliases(resolved.get("code")))
    left_name = _us_name_token(fallback.get("name"))
    right_name = _us_name_token(resolved.get("name"))
    name_match = bool(left_name and right_name and left_name == right_name)

    fq = abs(number(fallback.get("qty")))
    rq = abs(number(resolved.get("qty")))
    qty_match = fq > 0 and rq > 0 and abs(fq - rq) <= max(1e-8, 1e-6 * max(fq, rq))

    fp = abs(number(fallback.get("sell_price")))
    rp = abs(number(resolved.get("sell_price")))
    price_match = fp > 0 and rp > 0 and abs(fp - rp) / max(fp, rp) <= 0.0025

    if not (code_match or name_match or (qty_match and price_match and distance <= 2)):
        return None

    score = 0.0
    if code_match:
        score += 100.0
    if name_match:
        score += 50.0
    if qty_match:
        score += 20.0
    if price_match:
        score += 10.0
    score -= distance * 5.0
    return score


def reconcile_us_realized_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove U.S. transaction fallbacks once detailed P/L is available.

    A periodPnlDetail row is authoritative for realized P/L.  A
    transaction_fallback row exists only so that a visible SELL is not lost when
    P/L lookup is unavailable.  Once both can be matched, keep one detailed row
    and discard the fallback.  This also repairs duplicates already persisted by
    older builds.
    """
    rows = [dict(item) for item in events if isinstance(item, dict)]
    resolved_indexes = [
        index for index, row in enumerate(rows)
        if str(row.get("market") or "").upper() == "US"
        and row.get("source") == "period_pnl_detail"
        and row.get("pnl_available") is not False
        and row.get("realized_pnl_krw") not in (None, "")
    ]
    fallback_indexes = [
        index for index, row in enumerate(rows)
        if str(row.get("market") or "").upper() == "US"
        and row.get("source") == "transaction_fallback"
    ]
    used_resolved: set[int] = set()
    drop: set[int] = set()

    for fallback_index in fallback_indexes:
        fallback = rows[fallback_index]
        candidates: list[tuple[float, int]] = []
        for resolved_index in resolved_indexes:
            if resolved_index in used_resolved:
                continue
            score = _us_sale_match_score(fallback, rows[resolved_index])
            if score is not None:
                candidates.append((score, resolved_index))
        if not candidates:
            continue
        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score, resolved_index = candidates[0]
        # Avoid a low-information ambiguous match. Exact code/name candidates
        # naturally score well above this threshold.
        if best_score < 20.0:
            continue
        resolved = rows[resolved_index]
        for key in ("name", "qty", "buy_price", "sell_price", "fee_krw", "tax_krw"):
            if resolved.get(key) in (None, "", 0, 0.0) and fallback.get(key) not in (None, "", 0, 0.0):
                resolved[key] = fallback.get(key)
        # periodPnlDetail carries the U.S. market trade date, while the matching
        # dailyTransaction row can represent the Korea account calendar date.
        # Keep the original trade date for the transaction table, but use the
        # account date for chart alignment with total-asset snapshots.
        resolved.setdefault("trade_date", resolved.get("date"))
        fallback_account_date = "".join(ch for ch in str(fallback.get("account_date") or fallback.get("date") or "") if ch.isdigit())[:8]
        if len(fallback_account_date) == 8:
            resolved["account_date"] = fallback_account_date
        drop.add(fallback_index)
        used_resolved.add(resolved_index)

    output = [row for index, row in enumerate(rows) if index not in drop]
    deduped: dict[str, dict[str, Any]] = {}
    for row in output:
        row.setdefault("trade_date", row.get("date"))
        row["account_date"] = realized_account_date(row)
        event_id = str(row.get("id") or "")
        if event_id:
            deduped[event_id] = row
    return sorted(
        deduped.values(),
        key=lambda item: (str(item.get("date", "")), str(item.get("market", "")), str(item.get("code", ""))),
    )

def realized_summary(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = [dict(item) for item in events if isinstance(item, dict)]
    known = [
        item for item in rows
        if item.get("pnl_available") is not False and item.get("realized_pnl_krw") not in (None, "")
    ]
    wins = [number(item.get("return_pct")) for item in known if number(item.get("realized_pnl_krw")) > 0]
    losses = [number(item.get("return_pct")) for item in known if number(item.get("realized_pnl_krw")) < 0]
    total = sum(number(item.get("realized_pnl_krw")) for item in known)
    return {
        "cumulative_realized_krw": total,
        "trade_count": len(rows),
        "pnl_trade_count": len(known),
        "pnl_unavailable_count": len(rows) - len(known),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": len(wins) / len(known) * 100.0 if known else 0.0,
        "average_take_profit_pct": sum(wins) / len(wins) if wins else 0.0,
        "average_stop_loss_pct": sum(losses) / len(losses) if losses else 0.0,
    }


def monthly_realized_performance(
    events: Iterable[dict[str, Any]], asset_history: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    pnl_by_month: dict[str, float] = {}
    for event in events:
        if event.get("pnl_available") is False or event.get("realized_pnl_krw") in (None, ""):
            continue
        month = realized_account_date(event)[:6]
        if len(month) == 6:
            pnl_by_month[month] = pnl_by_month.get(month, 0.0) + number(event.get("realized_pnl_krw"))
    first_asset_by_month: dict[str, float] = {}
    for point in sorted(asset_history, key=lambda item: str(item.get("at", ""))):
        if point.get("asset_recorded") is False or point.get("total_asset_krw") in (None, ""):
            continue
        month = "".join(character for character in str(point.get("at", ""))[:10] if character.isdigit())[:6]
        if len(month) == 6 and month not in first_asset_by_month:
            first_asset_by_month[month] = number(point.get("total_asset_krw"))
    out: list[dict[str, Any]] = []
    for month, pnl in sorted(pnl_by_month.items()):
        base = first_asset_by_month.get(month, 0.0)
        out.append({
            "month": f"{month[:4]}-{month[4:]}",
            "realized_pnl_krw": pnl,
            "return_pct": pnl / base * 100.0 if base else None,
            "base_asset_krw": base,
        })
    return out


def update_yield_history(
    history: Iterable[dict[str, Any]], *, totals: dict[str, Any], realized: dict[str, Any],
    cash_flows: Iterable[dict[str, Any]], at: datetime | None = None, limit: int = 43_800,
) -> list[dict[str, Any]]:
    now = _seoul_datetime(at)
    stamp = now.isoformat(timespec="minutes")
    flows = [dict(item) for item in cash_flows if isinstance(item, dict)]
    deposits = sum(number(item.get("amount_krw")) for item in flows if item.get("side") == "DEPOSIT")
    withdrawals = sum(number(item.get("amount_krw")) for item in flows if item.get("side") == "WITHDRAW")
    point = {
        "at": stamp,
        "snapshot_date": now.strftime("%Y%m%d"),
        "total_asset_krw": number(totals.get("total_asset_krw") or totals.get("evaluation_krw")),
        "evaluation_krw": number(totals.get("evaluation_krw")),
        "unrealized_pnl_krw": number(totals.get("unrealized_pnl_krw") or totals.get("pnl_krw")),
        "cumulative_realized_krw": number(realized.get("cumulative_realized_krw")),
        "net_cash_flow_krw": deposits - withdrawals,
        "asset_recorded": True,
        "kind": "asset_snapshot",
    }
    out = [dict(item) for item in history if isinstance(item, dict) and item.get("at")]
    if out and str(out[-1].get("at", ""))[:13] == stamp[:13]:
        out[-1] = point
    else:
        out.append(point)
    return out[-max(1, limit):]


def backfill_daily_realized_history(
    history: Iterable[dict[str, Any]], *, realized_events: Iterable[dict[str, Any]],
    cash_flows: Iterable[dict[str, Any]], start_date: date, end_date: date,
    limit: int = 43_800,
) -> list[dict[str, Any]]:
    """Backfill daily realized-P/L points without inventing historical asset values.

    NHPLUG provides recent realized events but not a historical daily total-asset series.
    These synthetic points therefore carry only cumulative realized P/L and cash-flow data;
    ``asset_recorded=False`` is used by the web chart to leave the asset line blank until
    a real account snapshot was actually captured.
    """
    if end_date < start_date:
        return [dict(item) for item in history if isinstance(item, dict) and item.get("at")]

    realized_rows = [dict(item) for item in realized_events if isinstance(item, dict)]
    flow_rows = [dict(item) for item in cash_flows if isinstance(item, dict)]

    def digits(value: Any) -> str:
        return "".join(character for character in str(value or "") if character.isdigit())[:8]

    realized_by_day: dict[str, float] = {}
    for item in realized_rows:
        if item.get("pnl_available") is False or item.get("realized_pnl_krw") in (None, ""):
            continue
        day = realized_account_date(item)
        if len(day) == 8:
            realized_by_day[day] = realized_by_day.get(day, 0.0) + number(item.get("realized_pnl_krw"))

    flow_by_day: dict[str, float] = {}
    for item in flow_rows:
        day = digits(item.get("date"))
        if len(day) != 8:
            continue
        signed = number(item.get("amount_krw")) if item.get("side") == "DEPOSIT" else -number(item.get("amount_krw"))
        flow_by_day[day] = flow_by_day.get(day, 0.0) + signed

    start_key = start_date.strftime("%Y%m%d")
    realized_running = sum(
        amount for day, amount in realized_by_day.items() if day < start_key
    )
    flow_running = sum(amount for day, amount in flow_by_day.items() if day < start_key)

    # Rebuild the rolling-window daily points so newly discovered events can correct them.
    kept: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict) or not item.get("at"):
            continue
        day = digits(item.get("at"))
        in_window = start_key <= day <= end_date.strftime("%Y%m%d")
        if item.get("kind") == "realized_daily" and in_window:
            continue
        kept.append(dict(item))

    day = start_date
    # Today is represented by the real live snapshot appended by update_yield_history().
    while day < end_date:
        day_key = day.strftime("%Y%m%d")
        realized_running += realized_by_day.get(day_key, 0.0)
        flow_running += flow_by_day.get(day_key, 0.0)
        kept.append({
            "at": f"{day.isoformat()}T23:59:00+09:00",
            "snapshot_date": day_key,
            "total_asset_krw": None,
            "evaluation_krw": None,
            "unrealized_pnl_krw": None,
            "cumulative_realized_krw": realized_running,
            "net_cash_flow_krw": flow_running,
            "asset_recorded": False,
            "kind": "realized_daily",
        })
        day += timedelta(days=1)

    kept.sort(key=lambda item: str(item.get("at", "")))
    return kept[-max(1, limit):]


def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    return PBKDF2HMAC(
        algorithm=SHA256(), length=32, salt=salt, iterations=iterations,
    ).derive(password.encode("utf-8"))


def encrypt_payload(
    payload: dict[str, Any], password: str, *, salt: bytes | None = None, iv: bytes | None = None,
) -> dict[str, Any]:
    if len(password) != 4 or not password.isascii() or not password.isdigit():
        raise ValueError("포트폴리오 PIN은 숫자 4자리여야 합니다.")
    salt = salt or os.urandom(16)
    iv = iv or os.urandom(12)
    key = _derive_key(password, salt, PBKDF2_ITERATIONS)
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(iv, plaintext, ENVELOPE_AAD)
    return {
        "version": 1,
        "updated_at": payload.get("updated_at"),
        "kdf": {
            "name": "PBKDF2",
            "hash": "SHA-256",
            "iterations": PBKDF2_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
        },
        "cipher": {
            "name": "AES-GCM",
            "iv": base64.b64encode(iv).decode("ascii"),
            "aad": ENVELOPE_AAD.decode("ascii"),
        },
        "data": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_envelope(envelope: dict[str, Any], password: str) -> dict[str, Any]:
    if envelope.get("empty"):
        return {}
    if int(envelope.get("version", 0)) != 1:
        raise ValueError("지원하지 않는 암호화 데이터 버전입니다.")
    kdf = envelope.get("kdf") or {}
    cipher = envelope.get("cipher") or {}
    iterations = int(kdf.get("iterations", 0))
    if iterations < 100_000:
        raise ValueError("안전하지 않은 키 파생 설정입니다.")
    salt = base64.b64decode(kdf["salt"])
    iv = base64.b64decode(cipher["iv"])
    ciphertext = base64.b64decode(envelope["data"])
    key = _derive_key(password, salt, iterations)
    plaintext = AESGCM(key).decrypt(iv, ciphertext, ENVELOPE_AAD)
    decoded = json.loads(plaintext.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("복호화 데이터 형식이 올바르지 않습니다.")
    return decoded
