from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from src.config import WATCHLIST_UNIVERSE_FILE
from src.market_indicators import technical_chart_series, technical_snapshot, watchlist_sort_key
from src.nh_client import NhReadOnlyClient
from src.portfolio import number

TECHNICAL_BAR_COUNT = 520


def _is_bear_or_inverse(item: dict[str, Any]) -> bool:
    """Exclude inverse/bear/short leveraged ETFs from the ranking universe."""
    if str(item.get("kind") or "").lower() != "leveraged_etf":
        return False
    haystack = " ".join(
        str(item.get(key) or "") for key in ("name", "sector", "code")
    ).upper()
    return any(token in haystack for token in ("BEAR", "SHORT", "INVERSE"))


def load_universe(path: Path = WATCHLIST_UNIVERSE_FILE) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("관심종목 유니버스 설정 형식이 올바르지 않습니다.")
    return data


def _kr_market_cap(metadata: dict[str, Any]) -> float:
    raw = number(metadata.get("hts_avls"))
    if raw <= 0:
        return 0.0
    return raw if raw >= 100_000_000_000 else raw * 100_000_000.0


def _us_market_cap(metadata: dict[str, Any], price: float) -> tuple[float, float]:
    fx = number(metadata.get("fx_rate") or metadata.get("currency_prc"))
    if not 800 <= fx <= 2500:
        fx = number(os.getenv("USD_KRW_RATE"), 1_400.0)
    shares = number(metadata.get("list_num"))
    cap_usd = shares * price if shares > 0 and price > 0 else 0.0
    if cap_usd <= 0:
        cap_usd = number(metadata.get("list_amt") or metadata.get("list_amt_2"))
    return cap_usd * fx, fx


def _merge_by_id(old: Iterable[dict[str, Any]], new: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {str(item.get("id")): dict(item) for item in old if isinstance(item, dict) and item.get("id")}
    for item in new:
        if isinstance(item, dict) and item.get("id"):
            merged[str(item["id"])] = dict(item)
    return list(merged.values())


class MarketAnalyzer:
    def __init__(self, client: NhReadOnlyClient) -> None:
        self.client = client
        self._cache: dict[tuple[str, str], tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        self._universe = load_universe()
        self._configured = {
            (str(item.get("market", "")).upper(), str(item.get("code", "")).upper()): item
            for item in self._universe["items"]
            if isinstance(item, dict)
        }

    def quote(self, market: str, code: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        key = (market.upper(), code.upper())
        if key not in self._cache:
            self._cache[key] = self.client.market_bars(key[0], key[1], count=TECHNICAL_BAR_COUNT)
        return self._cache[key]

    def analyze(self, item: dict[str, Any]) -> dict[str, Any]:
        market = str(item.get("market") or "").upper()
        code = str(item.get("code") or "").upper()
        metadata, bars = self.quote(market, code)
        technical = technical_snapshot(bars)
        chart_bars = technical_chart_series(bars)
        price = number(technical.get("price"))
        if market == "KR":
            market_cap_krw = _kr_market_cap(metadata)
            sector = str(metadata.get("bstp_kor_isnm") or item.get("sector") or "기타")
            name = str(metadata.get("iem_nm") or item.get("name") or code)
            change_pct = number(metadata.get("prdy_ctrt"), number(technical.get("change_pct")))
            currency = "KRW"
            fx_rate = 1.0
        else:
            market_cap_krw, fx_rate = _us_market_cap(metadata, price)
            sector = str(metadata.get("industry_name") or item.get("sector") or "Other")
            name = str(metadata.get("kor_name") or item.get("name") or code)
            change_pct = number(metadata.get("pctchng"), number(technical.get("change_pct")))
            currency = "USD"
        return {
            "id": f"{market}:{code}",
            "market": market,
            "code": code,
            "name": name,
            "kind": str(item.get("kind") or "company"),
            "sector": sector,
            "currency": currency,
            "market_cap_krw": market_cap_krw,
            "fx_rate": fx_rate,
            **technical,
            "chart_bars": chart_bars,
            "change_pct": change_pct,
            "stale": False,
        }

    def enrich_holdings(self, holdings: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        enriched: list[dict[str, Any]] = []
        warnings: list[str] = []
        for raw in holdings:
            holding = dict(raw)
            market = str(holding.get("market") or "").upper()
            code = str(holding.get("code") or "").upper()
            configured = self._configured.get((market, code), {})
            try:
                metadata, bars = self.quote(market, code)
                technical = technical_snapshot(bars)
                chart_bars = technical_chart_series(bars)
                sector = (
                    metadata.get("bstp_kor_isnm") if market == "KR"
                    else metadata.get("industry_name")
                )
                holding.update({
                    "sector": str(sector or configured.get("sector") or ("ETF" if "ETF" in str(holding.get("name", "")).upper() else "기타")),
                    "band": technical.get("band"),
                    "band_display": technical.get("band_display") or technical.get("band"),
                    "band_weekly": technical.get("band_weekly"),
                    "band_monthly": technical.get("band_monthly"),
                    "band_position": technical.get("band_position"),
                    "st_14_3": technical.get("st_14_3"),
                    "ma60_slope_pct": technical.get("ma60_slope_pct"),
                    "ma200_slope_pct": technical.get("ma200_slope_pct"),
                    "vs_sma60_pct": technical.get("vs_sma60_pct"),
                    "chart_bars": chart_bars,
                })
            except Exception as exc:
                holding.update({
                    "sector": str(configured.get("sector") or "기타"),
                    "band": "조회 실패",
                    "band_display": "조회 실패",
                    "band_weekly": "조회 실패",
                    "band_monthly": "조회 실패",
                    "st_14_3": "UNKNOWN",
                    "ma60_slope_pct": None,
                    "ma200_slope_pct": None,
                    "vs_sma60_pct": None,
                    "chart_bars": [],
                })
                warnings.append(f"{market} {code} 기술지표 조회 실패: {exc}")
            enriched.append(holding)
        return enriched, warnings

    def realized_chart_map(
        self, events: Iterable[dict[str, Any]], *, max_unique: int = 100,
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """Build one compact technical chart per unique realized instrument.

        Charts live in a separate payload map instead of being copied into every
        realized-event row. This keeps the encrypted history small as the event
        ledger grows over time. ``quote`` is cached per market/code, so current
        holdings and realized rows reuse the same API result within an update.
        """
        rows = [dict(item) for item in events if isinstance(item, dict)]
        warnings: list[str] = []
        charts: dict[str, dict[str, Any]] = {}
        for row in reversed(rows):
            market = str(row.get("market") or "").upper()
            code = str(row.get("code") or "").upper()
            if not market or not code:
                continue
            identifier = f"{market}:{code}"
            if identifier in charts:
                continue
            if len(charts) >= max_unique:
                break
            try:
                _metadata, bars = self.quote(market, code)
                charts[identifier] = {
                    "market": market,
                    "code": code,
                    "name": str(row.get("name") or code),
                    "currency": str(row.get("currency") or ("USD" if market == "US" else "KRW")),
                    "chart_bars": technical_chart_series(bars),
                }
            except Exception as exc:
                warnings.append(f"{market} {code} 차트 조회 실패: {exc}")
        return charts, warnings

    def build_watchlist(self, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        previous = previous if isinstance(previous, dict) else {}
        old_rows = {
            str(item.get("id")): dict(item)
            for item in previous.get("items", [])
            if isinstance(item, dict) and item.get("id")
        }
        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        us_threshold = number(self._universe.get("us_market_cap_min_krw"))
        kr_threshold = number(self._universe.get("kr_market_cap_min_krw"))
        scanned = 0
        for item in self._universe["items"]:
            if not isinstance(item, dict):
                continue
            if _is_bear_or_inverse(item):
                continue
            scanned += 1
            identifier = f"{str(item.get('market', '')).upper()}:{str(item.get('code', '')).upper()}"
            try:
                row = self.analyze(item)
                threshold = kr_threshold if row["market"] == "KR" else us_threshold
                if row["kind"] == "leveraged_etf" or number(row.get("market_cap_krw")) >= threshold:
                    rows.append(row)
            except Exception as exc:
                fallback = old_rows.get(identifier)
                if fallback:
                    fallback["stale"] = True
                    rows.append(fallback)
                warnings.append(f"{identifier} 조회 실패: {exc}")
        rows.sort(key=watchlist_sort_key)
        now = datetime.now().astimezone()
        return {
            "schema_version": 1,
            "updated_at": now.isoformat(timespec="seconds"),
            "method": {
                "bollinger": "20일·2표준편차 채널을 동일 폭 5단계로 구분",
                "ma_slope": "각 이동평균의 최근 5거래일 변화율",
                "score_max": 60,
                "bollinger_timeframes": "일봉/주봉/월봉 BB20·2 최하단은 각각 +10점",
                "sorting": "밴드 단계와 무관하게 총점 내림차순",
            },
            "thresholds": {
                "us_market_cap_krw": us_threshold,
                "kr_market_cap_krw": kr_threshold,
            },
            "scanned_count": scanned,
            "eligible_count": len(rows),
            "items": rows,
            "warnings": warnings[:50],
            "source": "NH투자증권 Namuh PLUG 시세 조회",
        }
