from __future__ import annotations

"""Exact-source daily candle adapters for DTC.

KR/KR_ETF
    TossInvest public WTS c-chart candles (the same candle surface used by
    tossinvest.com), with ``useAdjustedRate=true``.

US/US_ETF
    TradingView public/anonymous chart websocket series.  The symbol is resolved
    with ``session=regular`` and ``adjustment=splits`` so the scanner uses the
    same data policy as the embedded anonymous TradingView chart shown by DTC.

There is deliberately *no Yahoo OHLC fallback*.  If an exact source is not
available, callers receive no frame and the scanner's coverage guard prevents a
mixed-source publication.
"""

import json
import math
import random
import re
import string
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests

try:
    import websocket  # websocket-client
except Exception:  # pragma: no cover - surfaced at runtime by the US adapter
    websocket = None

TOSS_WTS_BASE = "https://wts-info-api.tossinvest.com"
TOSS_C_CHART_MAX = 500
TOSS_WORKERS = 6
TOSS_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.tossinvest.com",
    "Referer": "https://www.tossinvest.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/151 Safari/537.36"
    ),
}

TRADINGVIEW_WS = "wss://data.tradingview.com/socket.io/websocket"
TRADINGVIEW_ORIGIN = "https://data.tradingview.com"
TV_SERIES_PER_SOCKET = 16


class ExactMarketDataError(RuntimeError):
    pass


def market_data_source_for(category: str) -> str:
    cat = str(category).upper()
    if cat in {"KR", "KR_ETF"}:
        return "TOSS_WTS_C_CHART_ADJUSTED"
    if cat in {"US", "US_ETF"}:
        return "TRADINGVIEW_PUBLIC_CHART_REGULAR_SPLITS"
    raise ValueError(f"unsupported category: {category}")


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _first(candle: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in candle and candle[key] is not None:
            return candle[key]
    return None


def _frame_from_rows(rows: list[tuple[pd.Timestamp, float, float, float, float, float]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"]).set_index("Date")
    df.index = pd.DatetimeIndex(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Volume"] = df["Volume"].fillna(0.0).clip(lower=0.0)
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df[(df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)]


# ---------------------------------------------------------------------------
# Toss WTS c-chart — KR/KR ETF
# ---------------------------------------------------------------------------


def toss_product_code(stock: Any) -> str:
    symbol = str(getattr(stock, "symbol", "") or getattr(stock, "ticker", "")).upper().strip()
    symbol = re.sub(r"\.(KS|KQ)$", "", symbol)
    if symbol.startswith("A") and len(symbol) == 7:
        return symbol
    if re.fullmatch(r"[0-9A-Z]{6}", symbol):
        return f"A{symbol}"
    raise ExactMarketDataError(f"invalid KR Toss product code: {symbol}")


def _parse_toss_candles(candles: Any) -> pd.DataFrame:
    if not isinstance(candles, list):
        return pd.DataFrame()
    rows: list[tuple[pd.Timestamp, float, float, float, float, float]] = []
    for c in candles:
        if not isinstance(c, dict):
            continue
        raw_dt = _first(c, "dt", "timestamp", "dateTime", "datetime")
        try:
            ts = pd.Timestamp(raw_dt)
            if pd.isna(ts):
                continue
            # Toss daily candles are exchange-local.  Preserve the visible
            # trading date and make the scanner index timezone-naive.
            if ts.tzinfo is not None:
                ts = ts.tz_convert("Asia/Seoul")
            ts = pd.Timestamp(ts.date())
        except Exception:
            continue
        o = _num(_first(c, "open", "openPrice", "openingPrice"))
        h = _num(_first(c, "high", "highPrice"))
        l = _num(_first(c, "low", "lowPrice"))
        cl = _num(_first(c, "close", "closePrice"))
        vol = _num(_first(c, "volume", "accumulatedTradingVolume")) or 0.0
        if None in (o, h, l, cl):
            continue
        rows.append((ts, float(o), float(h), float(l), float(cl), float(vol)))
    return _frame_from_rows(rows)


def _toss_page(product_code: str, count: int, from_datetime: str | None, timeout: float) -> tuple[pd.DataFrame, str | None]:
    params: dict[str, Any] = {
        "count": int(min(TOSS_C_CHART_MAX, max(1, count))),
        "session": "all",
        "investMode": "krx",
        "useAdjustedRate": "true",
    }
    if from_datetime:
        params["from"] = from_datetime
    url = f"{TOSS_WTS_BASE}/api/v1/c-chart/kr-s/{product_code}/day:1?{urlencode(params)}"
    response = requests.get(url, headers=TOSS_HEADERS, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        result = payload if isinstance(payload, dict) else {}
    candles = result.get("candles") or []
    frame = _parse_toss_candles(candles)

    # Some chart responses expose a cursor; prefer it.  If absent, the caller
    # derives a cursor from the oldest returned candle.
    cursor = result.get("nextFrom") or result.get("nextBefore") or result.get("nextDateTime")
    return frame, str(cursor) if cursor else None


def fetch_toss_kr_daily(stock: Any, bars: int = 720, timeout: float = 28.0) -> pd.DataFrame:
    code = toss_product_code(stock)
    collected = pd.DataFrame()
    cursor: str | None = None
    oldest_seen: pd.Timestamp | None = None

    # c-chart count is capped at 500. Two pages normally cover the >=604 bars
    # DTC needs.  Keep two extra attempts for inclusivity/cursor drift.
    for _ in range(4):
        page, next_cursor = _toss_page(code, min(TOSS_C_CHART_MAX, bars), cursor, timeout)
        if page.empty:
            break
        collected = pd.concat([collected, page]) if not collected.empty else page.copy()
        collected = collected[~collected.index.duplicated(keep="last")].sort_index()
        if len(collected) >= bars:
            return collected.tail(bars)

        new_oldest = pd.Timestamp(page.index.min())
        if oldest_seen is not None and new_oldest >= oldest_seen:
            break
        oldest_seen = new_oldest
        if next_cursor:
            cursor = next_cursor
        else:
            # Observed c-chart supports a ``from`` cursor.  Move just before the
            # oldest candle so an inclusive endpoint cannot return the same page.
            cursor = (new_oldest - pd.Timedelta(seconds=1)).isoformat()
        time.sleep(random.uniform(0.04, 0.12))
    return collected.tail(bars) if not collected.empty else pd.DataFrame()


def _download_toss_group(stocks: list[Any], bars: int, timeout: float) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=min(TOSS_WORKERS, max(1, len(stocks)))) as pool:
        futs = {pool.submit(fetch_toss_kr_daily, stock, bars, timeout): stock for stock in stocks}
        for fut in as_completed(futs):
            stock = futs[fut]
            try:
                df = fut.result()
                if not df.empty:
                    out[str(stock.ticker)] = df
            except Exception:
                # Per-symbol failures are reflected in scanner coverage/retry.
                pass
    return out


# ---------------------------------------------------------------------------
# TradingView public chart websocket — US/US ETF
# ---------------------------------------------------------------------------


def tradingview_symbol(stock: Any) -> str:
    raw = str(getattr(stock, "symbol", "") or getattr(stock, "ticker", "")).strip().upper()
    raw = raw.replace("-", ".") if "." not in raw and "-" in raw else raw
    exchange = str(getattr(stock, "exchange", "") or "").strip().upper()
    if "NASDAQ" in exchange:
        prefix = "NASDAQ"
    elif exchange == "NYSE":
        prefix = "NYSE"
    elif "NYSE AMERICAN" in exchange or "NYSE ARCA" in exchange or exchange in {"AMEX", "ARCA"}:
        prefix = "AMEX"
    elif "CBOE" in exchange or "BZX" in exchange or exchange == "BATS":
        prefix = "BATS"
    elif "IEX" in exchange:
        prefix = "IEX"
    else:
        # Nasdaq Trader's NASDAQ file has an explicit exchange; this branch is
        # only for stale cache rows.  Keep deterministic instead of probing a
        # different provider.
        prefix = "NASDAQ"
    return f"{prefix}:{raw}"


def _tv_session(prefix: str) -> str:
    return prefix + "_" + "".join(random.choice(string.ascii_lowercase) for _ in range(12))


def _tv_frame(payload: str) -> str:
    return f"~m~{len(payload)}~m~{payload}"


def _tv_command(method: str, params: list[Any]) -> str:
    return _tv_frame(json.dumps({"m": method, "p": params}, separators=(",", ":")))


def _tv_payloads(raw: str) -> list[str]:
    """Extract TradingView ~m~length~m~ payloads from one websocket receive."""
    text = str(raw or "")
    out: list[str] = []
    pos = 0
    marker = "~m~"
    while True:
        start = text.find(marker, pos)
        if start < 0:
            break
        len_start = start + len(marker)
        len_end = text.find(marker, len_start)
        if len_end < 0:
            break
        try:
            size = int(text[len_start:len_end])
        except ValueError:
            pos = len_end + len(marker)
            continue
        body_start = len_end + len(marker)
        body = text[body_start:body_start + size]
        if len(body) < size:
            break
        out.append(body)
        pos = body_start + size
    return out


def _tv_rows_from_series(series_payload: Any) -> pd.DataFrame:
    if not isinstance(series_payload, dict):
        return pd.DataFrame()
    points = series_payload.get("s") or []
    rows: list[tuple[pd.Timestamp, float, float, float, float, float]] = []
    if not isinstance(points, list):
        return pd.DataFrame()
    for point in points:
        if not isinstance(point, dict):
            continue
        values = point.get("v")
        if not isinstance(values, list) or len(values) < 5:
            continue
        timestamp = _num(values[0])
        o = _num(values[1]) if len(values) > 1 else None
        h = _num(values[2]) if len(values) > 2 else None
        l = _num(values[3]) if len(values) > 3 else None
        c = _num(values[4]) if len(values) > 4 else None
        vol = _num(values[5]) if len(values) > 5 else 0.0
        if timestamp is None or None in (o, h, l, c):
            continue
        try:
            # TradingView daily timestamps are exchange-session timestamps.
            ts = pd.Timestamp(float(timestamp), unit="s", tz="UTC").tz_convert("America/New_York")
            ts = pd.Timestamp(ts.date())
        except Exception:
            continue
        rows.append((ts, float(o), float(h), float(l), float(c), float(vol or 0.0)))
    return _frame_from_rows(rows)


def fetch_tradingview_us_batch(stocks: list[Any], bars: int = 720, timeout: float = 42.0) -> dict[str, pd.DataFrame]:
    if websocket is None:
        raise ExactMarketDataError("websocket-client is required for TradingView US candles")
    if not stocks:
        return {}

    chart_session = _tv_session("cs")
    results: dict[str, pd.DataFrame] = {}
    series_to_stock: dict[str, Any] = {}
    completed: set[str] = set()

    ws = websocket.create_connection(
        TRADINGVIEW_WS,
        timeout=timeout,
        origin=TRADINGVIEW_ORIGIN,
        header=["User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36"],
    )
    try:
        ws.send(_tv_command("set_auth_token", ["unauthorized_user_token"]))
        ws.send(_tv_command("chart_create_session", [chart_session, ""]))
        ws.send(_tv_command("switch_timezone", [chart_session, "exchange"]))

        for i, stock in enumerate(stocks):
            alias = f"sym_{i}"
            series = f"ser_{i}"
            tv_symbol = tradingview_symbol(stock)
            descriptor = "=" + json.dumps(
                {"symbol": tv_symbol, "adjustment": "splits", "session": "regular"},
                separators=(",", ":"),
            )
            ws.send(_tv_command("resolve_symbol", [chart_session, alias, descriptor]))
            ws.send(_tv_command("create_series", [chart_session, series, series, alias, "1D", int(bars)]))
            series_to_stock[series] = stock

        deadline = time.monotonic() + timeout
        while len(completed) < len(series_to_stock) and time.monotonic() < deadline:
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw:
                continue
            for payload in _tv_payloads(raw):
                if payload.startswith("~h~"):
                    try:
                        ws.send(_tv_frame(payload))
                    except Exception:
                        pass
                    continue
                try:
                    msg = json.loads(payload)
                except Exception:
                    continue
                method = msg.get("m")
                params = msg.get("p") or []
                if method == "timescale_update" and len(params) >= 2 and isinstance(params[1], dict):
                    for series, series_payload in params[1].items():
                        stock = series_to_stock.get(series)
                        if stock is None:
                            continue
                        frame = _tv_rows_from_series(series_payload)
                        if frame.empty:
                            continue
                        ticker = str(stock.ticker)
                        prior = results.get(ticker)
                        if prior is not None and not prior.empty:
                            frame = pd.concat([prior, frame])
                            frame = frame[~frame.index.duplicated(keep="last")].sort_index()
                        results[ticker] = frame.tail(bars)
                elif method == "series_completed" and len(params) >= 2:
                    series = str(params[1])
                    if series in series_to_stock:
                        completed.add(series)
                elif method in {"series_error", "critical_error"}:
                    # The coverage guard/retry path will surface missing symbols.
                    if len(params) >= 2 and str(params[1]) in series_to_stock:
                        completed.add(str(params[1]))
        return results
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _download_tradingview_group(stocks: list[Any], bars: int, timeout: float) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for start in range(0, len(stocks), TV_SERIES_PER_SOCKET):
        group = stocks[start:start + TV_SERIES_PER_SOCKET]
        try:
            out.update(fetch_tradingview_us_batch(group, bars=bars, timeout=timeout))
        except Exception:
            # Leave symbols absent so scanner retries them with a smaller batch.
            pass
        if start + TV_SERIES_PER_SOCKET < len(stocks):
            time.sleep(random.uniform(0.08, 0.18))
    return out


def download_market_frames(stocks: Iterable[Any], category: str, bars: int = 720, timeout: float = 42.0) -> dict[str, pd.DataFrame]:
    stock_list = list(stocks)
    cat = str(category).upper()
    if cat in {"KR", "KR_ETF"}:
        return _download_toss_group(stock_list, bars=bars, timeout=min(timeout, 30.0))
    if cat in {"US", "US_ETF"}:
        return _download_tradingview_group(stock_list, bars=bars, timeout=timeout)
    raise ValueError(f"unsupported category: {category}")
