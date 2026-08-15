from __future__ import annotations

import io
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

KR_CACHE = DATA_DIR / "universe_kr.json"
US_CACHE = DATA_DIR / "universe_us.json"

KIND_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131 Safari/537.36"
    )
}


@dataclass(frozen=True)
class Stock:
    ticker: str
    symbol: str
    name: str
    market: str
    currency: str
    exchange: str


def _save_cache(path: Path, stocks: Iterable[Stock]) -> None:
    payload = [asdict(s) for s in stocks]
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _load_cache(path: Path) -> list[Stock]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Stock(**row) for row in data]
    except Exception:
        return []


def _kr_market(market_type: str, suffix: str, exchange: str) -> list[Stock]:
    response = requests.get(
        KIND_URL,
        params={"method": "download", "searchType": "13", "marketType": market_type},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    tables = pd.read_html(io.BytesIO(response.content), header=0)
    if not tables:
        raise RuntimeError(f"KRX table not found: {exchange}")

    df = tables[0]
    code_col = next((c for c in df.columns if "종목코드" in str(c)), None)
    name_col = next((c for c in df.columns if "회사명" in str(c)), None)
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
        if "스팩" in name:
            continue
        stocks.append(
            Stock(
                ticker=f"{code}{suffix}",
                symbol=code,
                name=name,
                market="KR",
                currency="KRW",
                exchange=exchange,
            )
        )
    return stocks


def fetch_kr_universe() -> tuple[list[Stock], str]:
    try:
        stocks = _kr_market("stockMkt", ".KS", "KOSPI") + _kr_market("kosdaqMkt", ".KQ", "KOSDAQ")
        dedup = {s.ticker: s for s in stocks}
        result = sorted(dedup.values(), key=lambda s: (s.exchange, s.symbol))
        if len(result) < 1000:
            raise RuntimeError(f"KRX universe unexpectedly small: {len(result)}")
        _save_cache(KR_CACHE, result)
        return result, "KRX"
    except Exception as exc:
        cached = _load_cache(KR_CACHE)
        if cached:
            print(f"KRX listing failed; using cache: {exc}")
            return cached, "CACHE"
        raise


def _read_pipe(url: str) -> pd.DataFrame:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return pd.read_csv(io.StringIO(response.text), sep="|", dtype=str)


def _is_us_equity(name: str, symbol: str) -> bool:
    if not name or not symbol:
        return False
    if not re.fullmatch(r"[A-Z0-9.\-]+", symbol):
        return False
    blocked = re.compile(
        r"\b(warrant|warrants|right|rights|unit|units|preferred|debenture|debentures|bond|bonds)\b|notes? due",
        re.IGNORECASE,
    )
    return blocked.search(name) is None


def fetch_us_universe() -> tuple[list[Stock], str]:
    try:
        nasdaq = _read_pipe(NASDAQ_LISTED_URL)
        other = _read_pipe(OTHER_LISTED_URL)
        stocks: list[Stock] = []

        for _, row in nasdaq.iterrows():
            symbol = str(row.get("Symbol", "")).strip()
            name = str(row.get("Security Name", "")).strip()
            if symbol.startswith("File Creation Time"):
                continue
            if str(row.get("ETF", "N")).strip() != "N" or str(row.get("Test Issue", "N")).strip() != "N":
                continue
            if not _is_us_equity(name, symbol):
                continue
            ticker = symbol.replace(".", "-")
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
            if symbol.startswith("File Creation Time"):
                continue
            if str(row.get("ETF", "N")).strip() != "N" or str(row.get("Test Issue", "N")).strip() != "N":
                continue
            if not _is_us_equity(name, symbol):
                continue
            ticker = symbol.replace(".", "-")
            stocks.append(Stock(ticker, symbol, name, "US", "USD", exchange_names.get(exchange_code, exchange_code or "US")))

        dedup = {s.ticker: s for s in stocks}
        result = sorted(dedup.values(), key=lambda s: (s.exchange, s.symbol))
        if len(result) < 3000:
            raise RuntimeError(f"US universe unexpectedly small: {len(result)}")
        _save_cache(US_CACHE, result)
        return result, "NASDAQ_TRADER"
    except Exception as exc:
        cached = _load_cache(US_CACHE)
        if cached:
            print(f"US listing failed; using cache: {exc}")
            return cached, "CACHE"
        raise


def get_universe(market: str) -> tuple[list[Stock], str]:
    market = market.upper()
    if market == "KR":
        return fetch_kr_universe()
    if market == "US":
        return fetch_us_universe()
    raise ValueError(f"Unsupported market: {market}")
