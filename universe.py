from __future__ import annotations

import html as html_lib
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests
from lxml import html as lxml_html

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

KR_CACHE = DATA_DIR / "universe_kr.json"
US_CACHE = DATA_DIR / "universe_us.json"
US_ETF_CACHE = DATA_DIR / "universe_us_etf.json"

KIND_CORP_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do"
KIND_ADMIN_URL = "https://kind.krx.co.kr/investwarn/adminissue.do"
KIND_HALT_URL = "https://kind.krx.co.kr/investwarn/tradinghaltissue.do"
KIND_WARNING_URL = "https://kind.krx.co.kr/investwarn/investattentwarnrisky.do"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
NASDAQ_HALT_RSS = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

KR_BLOCK_KEYWORDS = (
    "관리종목",
    "투자주의",
    "투자경고",
    "투자위험",
    "매매거래정지",
    "거래정지",
    "정리매매",
)


@dataclass(frozen=True)
class Stock:
    ticker: str
    symbol: str
    name: str
    category: str  # KR / US / US_ETF
    currency: str
    exchange: str
    listed_date: str | None = None


def _save_cache(path: Path, stocks: Iterable[Stock]) -> None:
    path.write_text(
        json.dumps([asdict(s) for s in stocks], ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _load_cache(path: Path) -> list[Stock]:
    if not path.exists():
        return []
    try:
        return [Stock(**row) for row in json.loads(path.read_text(encoding="utf-8"))]
    except Exception:
        return []


def _is_kr_preferred(name: str) -> bool:
    compact = re.sub(r"\s+", "", name or "")
    if "우선주" in compact:
        return True
    # 현대차2우B, LG화학우, 삼성전자우 등 일반적인 우선주 표기
    return bool(re.search(r"(?:\d+)?우(?:B|C)?$", compact, flags=re.IGNORECASE))


def _kr_market(market_type: str, suffix: str, exchange: str) -> list[Stock]:
    response = requests.get(
        KIND_CORP_URL,
        params={"method": "download", "searchType": "13", "marketType": market_type},
        headers=HEADERS,
        timeout=40,
    )
    response.raise_for_status()
    tables = pd.read_html(io.BytesIO(response.content), header=0)
    if not tables:
        raise RuntimeError(f"KRX table not found: {exchange}")

    df = tables[0]
    code_col = next((c for c in df.columns if "종목코드" in str(c)), None)
    name_col = next((c for c in df.columns if "회사명" in str(c)), None)
    list_col = next((c for c in df.columns if "상장일" in str(c)), None)
    if code_col is None or name_col is None:
        raise RuntimeError(f"KRX columns not found: {list(df.columns)}")

    stocks: list[Stock] = []
    for _, row in df.iterrows():
        raw_code = str(row.get(code_col, "")).strip()
        code = re.sub(r"\.0$", "", raw_code)
        code = re.sub(r"\D", "", code).zfill(6)
        name = str(row.get(name_col, "")).strip()
        if not re.fullmatch(r"\d{6}", code) or not name or name.lower() == "nan":
            continue
        if "스팩" in name or _is_kr_preferred(name):
            continue

        listed_date = None
        if list_col is not None:
            parsed = pd.to_datetime(row.get(list_col), errors="coerce")
            if pd.notna(parsed):
                listed_date = parsed.date().isoformat()

        stocks.append(
            Stock(
                ticker=f"{code}{suffix}",
                symbol=code,
                name=name,
                category="KR",
                currency="KRW",
                exchange=exchange,
                listed_date=listed_date,
            )
        )
    return stocks


def fetch_kr_universe() -> tuple[list[Stock], str]:
    try:
        stocks = _kr_market("stockMkt", ".KS", "KOSPI") + _kr_market("kosdaqMkt", ".KQ", "KOSDAQ")
        result = sorted({s.ticker: s for s in stocks}.values(), key=lambda s: (s.exchange, s.symbol))
        if len(result) < 1000:
            raise RuntimeError(f"KRX universe unexpectedly small: {len(result)}")
        _save_cache(KR_CACHE, result)
        return result, "KRX_KIND"
    except Exception as exc:
        cached = _load_cache(KR_CACHE)
        if cached:
            print(f"KRX listing failed; using cache: {exc}")
            return cached, "CACHE"
        raise


def _extract_codes_and_names(page: str, name_to_code: dict[str, str]) -> set[str]:
    restricted: set[str] = set()
    try:
        root = lxml_html.fromstring(page)
    except Exception:
        return restricted

    for row in root.xpath("//tr"):
        text = " ".join(t.strip() for t in row.itertext() if t and t.strip())
        attrs = " ".join(
            str(v)
            for node in row.xpath(".//*")
            for key, v in node.attrib.items()
            if key.lower() in {"alt", "title", "class", "onclick", "href"}
        )
        combined = f"{text} {attrs}"
        if not any(k in combined for k in KR_BLOCK_KEYWORDS):
            continue
        for code in re.findall(r"(?<!\d)(\d{6})(?!\d)", combined):
            restricted.add(code)
        compact_text = re.sub(r"\s+", "", combined)
        for name, code in name_to_code.items():
            if name and re.sub(r"\s+", "", name) in compact_text:
                restricted.add(code)
    return restricted


def _scan_kind_corp_badges(stocks: list[Stock]) -> tuple[set[str], int]:
    """Scan KIND current-company pages and read current status badges.

    A large-page request is attempted first. If KIND ignores the requested page
    size, the function falls back to numbered pages. The caller rejects the KR
    refresh when too few listed codes are verified, preventing a silent bypass of
    step-0 regulatory-status filters.
    """
    restricted: set[str] = set()
    seen_codes: set[str] = set()
    name_to_code = {s.name: s.symbol for s in stocks}
    valid_symbols = {s.symbol for s in stocks}

    def parse_page(text: str) -> set[str]:
        page_codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", text)) & valid_symbols
        if len(page_codes) < 5:
            try:
                root = lxml_html.fromstring(text)
                normalized_page = " ".join(t.strip() for t in root.itertext() if t and t.strip())
                for name, code in name_to_code.items():
                    if name and name in normalized_page:
                        page_codes.add(code)
            except Exception:
                pass
        restricted.update(_extract_codes_and_names(text, name_to_code))
        return page_codes

    for market_type in ("stockMkt", "kosdaqMkt"):
        # Most KIND deployments honor currentPageSize. One bulk request is faster
        # and less prone to mid-pagination changes in current-status badges.
        response = requests.get(
            KIND_CORP_URL,
            params={
                "method": "loadInitPage",
                "searchType": "13",
                "marketType": market_type,
                "pageIndex": "1",
                "currentPageSize": "5000",
                "orderMode": "3",
                "orderStat": "D",
            },
            headers=HEADERS,
            timeout=40,
        )
        response.raise_for_status()
        bulk_codes = parse_page(response.text)
        seen_codes.update(bulk_codes)
        if len(bulk_codes) >= 300:
            continue

        previous_signature: tuple[str, ...] | None = None
        for page_index in range(1, 100):
            response = requests.get(
                KIND_CORP_URL,
                params={
                    "method": "loadInitPage",
                    "searchType": "13",
                    "marketType": market_type,
                    "pageIndex": str(page_index),
                    "currentPageSize": "100",
                    "orderMode": "3",
                    "orderStat": "D",
                },
                headers=HEADERS,
                timeout=35,
            )
            response.raise_for_status()
            page_codes = parse_page(response.text)
            signature = tuple(sorted(page_codes))
            if not signature or signature == previous_signature:
                break
            previous_signature = signature
            seen_codes.update(page_codes)
            if len(page_codes) < 20:
                break

    return restricted, len(seen_codes)


def _scan_kind_issue_page(url: str, method: str, stocks: list[Stock]) -> set[str]:
    name_to_code = {s.name: s.symbol for s in stocks}
    valid_symbols = {s.symbol for s in stocks}
    response = requests.get(
        url,
        params={"method": method, "currentPageSize": "3000", "pageIndex": "1"},
        headers=HEADERS,
        timeout=35,
    )
    response.raise_for_status()
    text = response.text

    found = _extract_codes_and_names(text, name_to_code)
    # Current issue pages can show the issue itself without repeating the category
    # word in every row. Read every row that clearly contains a listed code/name.
    try:
        root = lxml_html.fromstring(text)
        for row in root.xpath("//table//tr"):
            row_text = " ".join(t.strip() for t in row.itertext() if t and t.strip())
            attrs = " ".join(str(v) for n in row.xpath(".//*") for v in n.attrib.values())
            combined = f"{row_text} {attrs}"
            codes = re.findall(r"(?<!\d)(\d{6})(?!\d)", combined)
            found.update(c for c in codes if c in valid_symbols)
            compact = re.sub(r"\s+", "", combined)
            for name, code in name_to_code.items():
                if name and re.sub(r"\s+", "", name) in compact:
                    found.add(code)
    except Exception:
        pass
    return found


def fetch_kr_restricted_symbols(stocks: list[Stock]) -> tuple[set[str], dict]:
    """Return current KRX/KIND restricted symbols used by step-0 screening.

    The current-company badge crawl is the validation backbone. Separate official
    KIND issue pages are unioned to capture management and trading-halt rows even
    when the badge markup changes.
    """
    badge_restricted, scanned_codes = _scan_kind_corp_badges(stocks)
    if scanned_codes < 900:
        raise RuntimeError(
            f"KR restriction snapshot incomplete ({scanned_codes} listed codes seen). "
            "KR data was not published to avoid bypassing step-0 status filters."
        )

    restricted = set(badge_restricted)
    issue_sources = {}
    pages = [
        ("management", KIND_ADMIN_URL, "searchAdminIssueList"),
        ("halt", KIND_HALT_URL, "searchTradingHaltIssueMain"),
        ("warning", KIND_WARNING_URL, "investattentwarnriskyMain"),
    ]
    for label, url, method in pages:
        try:
            values = _scan_kind_issue_page(url, method, stocks)
            restricted.update(values)
            issue_sources[label] = len(values)
        except Exception as exc:
            # Badge crawl remains authoritative fallback; keep explicit metadata.
            issue_sources[label] = f"error:{type(exc).__name__}"
            print(f"KR issue page {label} unavailable: {exc}")

    return restricted, {
        "source": "KRX_KIND_CURRENT_STATUS",
        "listed_codes_verified": scanned_codes,
        "restricted_count": len(restricted),
        "issue_pages": issue_sources,
    }


def _read_pipe(url: str) -> pd.DataFrame:
    response = requests.get(url, headers=HEADERS, timeout=35)
    response.raise_for_status()
    return pd.read_csv(io.StringIO(response.text), sep="|", dtype=str)


def _valid_us_symbol(symbol: str) -> bool:
    return bool(symbol and re.fullmatch(r"[A-Z0-9.\-]+", symbol))


def _is_us_common_equity(name: str, symbol: str) -> bool:
    if not name or not _valid_us_symbol(symbol):
        return False
    blocked = re.compile(
        r"\b(warrant|warrants|right|rights|unit|units|preferred|preference|debenture|debentures|bond|bonds)\b"
        r"|notes? due|depositary shares?.*preferred",
        re.IGNORECASE,
    )
    return blocked.search(name) is None


def fetch_us_universes() -> tuple[list[Stock], list[Stock], str]:
    try:
        nasdaq = _read_pipe(NASDAQ_LISTED_URL)
        other = _read_pipe(OTHER_LISTED_URL)
        stocks: list[Stock] = []
        etfs: list[Stock] = []

        for _, row in nasdaq.iterrows():
            symbol = str(row.get("Symbol", "")).strip()
            name = str(row.get("Security Name", "")).strip()
            if symbol.startswith("File Creation Time") or str(row.get("Test Issue", "N")).strip() != "N":
                continue
            if not _valid_us_symbol(symbol):
                continue
            ticker = symbol.replace(".", "-")
            if str(row.get("ETF", "N")).strip() == "Y":
                etfs.append(Stock(ticker, symbol, name, "US_ETF", "USD", "NASDAQ"))
            elif _is_us_common_equity(name, symbol):
                stocks.append(Stock(ticker, symbol, name, "US", "USD", "NASDAQ"))

        exchange_names = {
            "N": "NYSE",
            "A": "NYSE American",
            "P": "NYSE Arca",
            "Z": "Cboe BZX",
            "V": "IEX",
        }
        for _, row in other.iterrows():
            symbol = str(row.get("ACT Symbol", "")).strip()
            name = str(row.get("Security Name", "")).strip()
            exchange_code = str(row.get("Exchange", "")).strip()
            if symbol.startswith("File Creation Time") or str(row.get("Test Issue", "N")).strip() != "N":
                continue
            if not _valid_us_symbol(symbol):
                continue
            ticker = symbol.replace(".", "-")
            exchange = exchange_names.get(exchange_code, exchange_code or "US")
            if str(row.get("ETF", "N")).strip() == "Y":
                etfs.append(Stock(ticker, symbol, name, "US_ETF", "USD", exchange))
            elif _is_us_common_equity(name, symbol):
                stocks.append(Stock(ticker, symbol, name, "US", "USD", exchange))

        stock_result = sorted({s.ticker: s for s in stocks}.values(), key=lambda s: (s.exchange, s.symbol))
        etf_result = sorted({s.ticker: s for s in etfs}.values(), key=lambda s: (s.exchange, s.symbol))
        if len(stock_result) < 2500:
            raise RuntimeError(f"US stock universe unexpectedly small: {len(stock_result)}")
        if len(etf_result) < 500:
            raise RuntimeError(f"US ETF universe unexpectedly small: {len(etf_result)}")
        _save_cache(US_CACHE, stock_result)
        _save_cache(US_ETF_CACHE, etf_result)
        return stock_result, etf_result, "NASDAQ_TRADER"
    except Exception as exc:
        stock_cache = _load_cache(US_CACHE)
        etf_cache = _load_cache(US_ETF_CACHE)
        if stock_cache and etf_cache:
            print(f"US symbol directory failed; using cache: {exc}")
            return stock_cache, etf_cache, "CACHE"
        raise


def fetch_us_halted_symbols() -> tuple[set[str], dict]:
    """Read Nasdaq Trader's current trade-halt RSS feed.

    The feed covers Nasdaq-listed and other exchange-listed securities. The parser
    accepts multiple historical RSS layouts so a markup tweak does not crash scans.
    """
    response = requests.get(NASDAQ_HALT_RSS, headers=HEADERS, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    halted: set[str] = set()
    item_count = 0
    for item in root.findall(".//item"):
        item_count += 1
        title = item.findtext("title") or ""
        description = html_lib.unescape(item.findtext("description") or "")
        blob = f"{title} {description}"
        patterns = [
            r"Issue\s*Symbol\s*(?:</?[^>]+>\s*)*[:\-]?\s*([A-Z0-9.\-]+)",
            r"\bSymbol\s*[:\-]\s*([A-Z0-9.\-]+)",
            r"Trade\s+Halt\s*[-:]\s*([A-Z0-9.\-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, blob, flags=re.IGNORECASE)
            if match:
                symbol = match.group(1).upper().replace(".", "-")
                if _valid_us_symbol(symbol):
                    halted.add(symbol)
                    break
    return halted, {"source": "NASDAQ_TRADER_HALT_RSS", "feed_items": item_count, "halted_count": len(halted)}


def get_universe(category: str) -> tuple[list[Stock], str]:
    category = category.upper()
    if category == "KR":
        return fetch_kr_universe()
    if category in {"US", "US_ETF"}:
        stocks, etfs, source = fetch_us_universes()
        return (stocks if category == "US" else etfs), source
    raise ValueError(f"Unsupported category: {category}")
