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
import os
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
TOSS_C_CHART_MAX = max(61, min(500, int(os.environ.get("DTC_TOSS_PAGE_SIZE", "200"))))
TOSS_WORKERS = max(1, min(6, int(os.environ.get("DTC_TOSS_WORKERS", "4"))))
TOSS_HTTP_RETRIES = max(1, min(3, int(os.environ.get("DTC_TOSS_HTTP_RETRIES", "2"))))
TOSS_RETRYABLE_STATUS = {500, 502, 503, 504}
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
TV_SERIES_PER_SOCKET = max(1, min(32, int(os.environ.get("DTC_TV_SYMBOLS_PER_SOCKET", "16"))))
TV_SOCKET_PAUSE = (0.35, 0.70)
TV_CONNECT_ORIGINS = ("https://data.tradingview.com", "https://www.tradingview.com")
TV_AUTH_TOKEN = os.environ.get("TRADINGVIEW_AUTH_TOKEN", "").strip() or "unauthorized_user_token"


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


def _response_detail(response: requests.Response, limit: int = 420) -> str:
    try:
        text = (response.text or "").strip().replace("\n", " ")
    except Exception:
        text = ""
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


def _toss_page(product_code: str, count: int, from_datetime: str | None, timeout: float) -> tuple[pd.DataFrame, str | None]:
    """Fetch one public Toss WTS candle page.

    Toss currently accepts small chart requests reliably, while a production-sized
    ``count`` can return HTTP 400 even for valid products.  Treat that specific
    case as a page-size negotiation problem: back off the count (never the product
    code or source) and remember a smaller request size for the caller.  403/429
    remain hard stops and are never bypassed.
    """
    requested = int(min(500, max(1, count)))
    candidates = []
    for n in (requested, min(requested, 200), min(requested, 120), min(requested, 61)):
        if n not in candidates:
            candidates.append(n)

    last_exc: Exception | None = None
    for effective_count in candidates:
        params: dict[str, Any] = {
            "count": effective_count,
            "session": "all",
            "investMode": "krx",
            "useAdjustedRate": "true",
        }
        if from_datetime:
            params["from"] = from_datetime
        url = f"{TOSS_WTS_BASE}/api/v1/c-chart/kr-s/{product_code}/day:1?{urlencode(params)}"

        for attempt in range(1, TOSS_HTTP_RETRIES + 1):
            try:
                response = requests.get(url, headers=TOSS_HEADERS, timeout=timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                if attempt < TOSS_HTTP_RETRIES:
                    time.sleep(0.8 * attempt)
                    continue
                raise ExactMarketDataError(
                    f"Toss c-chart transport failed for {product_code}: {type(exc).__name__}: {exc}"
                ) from exc

            if response.status_code >= 400:
                detail = _response_detail(response)
                msg = (
                    f"Toss c-chart HTTP {response.status_code} for {product_code}"
                    f" count={effective_count}{': ' + detail if detail else ''}"
                )
                if response.status_code == 400 and effective_count > 61:
                    last_exc = ExactMarketDataError(msg)
                    break  # try a smaller count, same exact source/product
                if response.status_code in TOSS_RETRYABLE_STATUS and attempt < TOSS_HTTP_RETRIES:
                    last_exc = ExactMarketDataError(msg)
                    time.sleep(0.8 * attempt)
                    continue
                if response.status_code in {403, 429}:
                    msg += "; public endpoint rejected/rate-limited this request; stop bulk retries"
                raise ExactMarketDataError(msg)

            try:
                payload = response.json()
            except ValueError as exc:
                raise ExactMarketDataError(
                    f"Toss c-chart returned non-JSON for {product_code}: {_response_detail(response)}"
                ) from exc

            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                raise ExactMarketDataError(
                    f"Toss c-chart unexpected response for {product_code}: missing object result"
                )
            candles = result.get("candles")
            if not isinstance(candles, list):
                raise ExactMarketDataError(
                    f"Toss c-chart unexpected response for {product_code}: result.candles is not a list"
                )
            frame = _parse_toss_candles(candles)
            if candles and frame.empty:
                keys = sorted({str(k) for c in candles[:3] if isinstance(c, dict) for k in c.keys()})
                raise ExactMarketDataError(
                    f"Toss c-chart candle schema unsupported for {product_code}; observed keys={keys[:20]}"
                )

            paging = result.get("pagingParam") if isinstance(result.get("pagingParam"), dict) else {}
            cursor = (
                result.get("nextFrom") or result.get("nextBefore") or result.get("nextDateTime")
                or paging.get("key") or paging.get("nextDateTime") or paging.get("from")
            )
            if effective_count != requested:
                print(f"[Toss] {product_code} page-size fallback {requested}->{effective_count} accepted")
            return frame, str(cursor) if cursor else None

    if last_exc is not None:
        raise ExactMarketDataError(f"Toss c-chart failed for {product_code}: {last_exc}")
    raise ExactMarketDataError(f"Toss c-chart failed for {product_code}")

def fetch_toss_kr_daily(stock: Any, bars: int = 720, timeout: float = 28.0) -> pd.DataFrame:
    code = toss_product_code(stock)
    collected = pd.DataFrame()
    cursor: str | None = None
    oldest_seen: pd.Timestamp | None = None

    # Page size is negotiated by _toss_page.  If Toss only accepts the small
    # 61-candle shape, 720 bars need ~12 pages; larger accepted pages exit much
    # earlier as soon as enough history is collected.
    max_pages = max(4, math.ceil(max(1, bars) / 61) + 2)
    for _ in range(max_pages):
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
            local_oldest = new_oldest.tz_localize("Asia/Seoul") if new_oldest.tzinfo is None else new_oldest.tz_convert("Asia/Seoul")
            cursor = (local_oldest - pd.Timedelta(seconds=1)).isoformat()
        time.sleep(random.uniform(0.04, 0.12))
    return collected.tail(bars) if not collected.empty else pd.DataFrame()


def _download_toss_group(stocks: list[Any], bars: int, timeout: float) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=min(TOSS_WORKERS, max(1, len(stocks)))) as pool:
        futs = {pool.submit(fetch_toss_kr_daily, stock, bars, timeout): stock for stock in stocks}
        for fut in as_completed(futs):
            stock = futs[fut]
            try:
                df = fut.result()
                if not df.empty:
                    out[str(stock.ticker)] = df
                else:
                    failures.append(f"{stock.ticker}: empty candle result")
            except Exception as exc:
                failures.append(f"{stock.ticker}: {type(exc).__name__}: {exc}")

    # A whole group failing is a shared transport/protocol event until proven
    # otherwise. Surface it immediately so scanner preflight/circuit-breaker can
    # stop instead of silently burning 30 minutes on thousands of requests.
    if stocks and not out and failures:
        summary = " | ".join(failures[:3])
        if len(failures) > 3:
            summary += f" | +{len(failures)-3} more failures"
        raise ExactMarketDataError(
            f"all Toss c-chart requests failed for {len(stocks)} symbols; {summary}"
        )
    if failures:
        print(f"[Toss] batch partial: priced={len(out)}/{len(stocks)}, failures={len(failures)}; {failures[0]}")
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
    # TradingView's frame length is the UTF-8 byte length, not Python's
    # character count. Most protocol JSON is ASCII, but using bytes here avoids
    # a subtle framing bug if a non-ASCII server/client payload ever appears.
    return f"~m~{len(payload.encode('utf-8'))}~m~{payload}"


def _tv_command(method: str, params: list[Any]) -> str:
    return _tv_frame(json.dumps({"m": method, "p": params}, separators=(",", ":")))


def _tv_payloads(raw: str | bytes) -> list[str]:
    """Extract TradingView ``~m~length~m~`` payloads from one websocket receive.

    One websocket text message can contain several protocol frames. Length is a
    byte count, so parsing is performed on bytes and decoded per frame.
    """
    if isinstance(raw, bytes):
        data = raw
    else:
        data = str(raw or "").encode("utf-8")
    out: list[str] = []
    pos = 0
    marker = b"~m~"
    while True:
        start = data.find(marker, pos)
        if start < 0:
            break
        len_start = start + len(marker)
        len_end = data.find(marker, len_start)
        if len_end < 0:
            break
        try:
            size = int(data[len_start:len_end].decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            pos = len_end + len(marker)
            continue
        body_start = len_end + len(marker)
        body = data[body_start:body_start + size]
        if len(body) < size:
            break
        try:
            out.append(body.decode("utf-8"))
        except UnicodeDecodeError:
            pass
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


def _tv_series_spec(index: int) -> tuple[str, str, str]:
    """Return protocol-correct ``(alias, series_id, series_key)`` identifiers."""
    n = int(index) + 1
    return f"sds_sym_{n}", f"sds_{n}", f"s{n}"


def _tv_connect(timeout: float):
    """Open a TradingView websocket with diagnostic origin fallback.

    TradingView is an unofficial/public websocket dependency and its handshake
    policy can vary by edge/IP. Trying the two browser-observed origins keeps an
    otherwise valid scan from failing on an origin-policy change while preserving
    the same TradingView source and anonymous/auth-token policy.
    """
    if websocket is None:
        raise ExactMarketDataError("websocket-client is required for TradingView US candles")

    errors: list[str] = []
    headers = [
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Accept-Language: en-US,en;q=0.9",
        "Cache-Control: no-cache",
        "Pragma: no-cache",
    ]
    for origin in TV_CONNECT_ORIGINS:
        try:
            return websocket.create_connection(
                TRADINGVIEW_WS,
                timeout=timeout,
                origin=origin,
                header=headers,
            )
        except Exception as exc:
            errors.append(f"origin={origin}: {type(exc).__name__}: {exc}")
    raise ExactMarketDataError("TradingView websocket handshake failed; " + " | ".join(errors))


def _tv_error_text(method: str, params: list[Any]) -> str:
    detail = " | ".join(str(x) for x in params[1:4]) if len(params) > 1 else "no detail"
    return f"{method}: {detail}"


def fetch_tradingview_us_batch(stocks: list[Any], bars: int = 720, timeout: float = 42.0) -> dict[str, pd.DataFrame]:
    """Fetch US candles over one websocket, one active series at a time.

    Anonymous TradingView chart sessions can currently reject a second concurrent
    ``create_series`` with ``exceed limit of series in the session``.  Reuse the
    websocket, but create/delete a dedicated chart session for each symbol so no
    session ever owns more than one series.
    """
    if websocket is None:
        raise ExactMarketDataError("websocket-client is required for TradingView US candles")
    if not stocks:
        return {}

    results: dict[str, pd.DataFrame] = {}
    symbol_errors: dict[str, str] = {}
    ws = _tv_connect(timeout)
    overall_deadline = time.monotonic() + float(timeout)
    try:
        try:
            ws.settimeout(min(6.0, max(2.0, float(timeout) / 5.0)))
        except Exception:
            pass
        ws.send(_tv_command("set_auth_token", [TV_AUTH_TOKEN]))
        ws.send(_tv_command("set_locale", ["en", "US"]))

        for i, stock in enumerate(stocks):
            if time.monotonic() >= overall_deadline:
                break
            chart_session = _tv_session("cs")
            alias, series_id, series_key = _tv_series_spec(i)
            ticker = str(stock.ticker)
            tv_symbol = tradingview_symbol(stock)
            descriptor = "=" + json.dumps(
                {"symbol": tv_symbol, "adjustment": "splits", "session": "regular"},
                separators=(",", ":"),
            )
            ws.send(_tv_command("chart_create_session", [chart_session, ""]))
            ws.send(_tv_command("switch_timezone", [chart_session, "exchange"]))
            ws.send(_tv_command("resolve_symbol", [chart_session, alias, descriptor]))
            ws.send(_tv_command("create_series", [chart_session, series_id, series_key, alias, "1D", int(bars)]))

            frame = pd.DataFrame()
            completed = False
            per_symbol_budget = max(4.0, min(10.0, float(timeout) / max(1, len(stocks)) * 2.0))
            symbol_deadline = min(overall_deadline, time.monotonic() + per_symbol_budget)
            fatal_error: str | None = None
            transport_error: str | None = None

            while not completed and time.monotonic() < symbol_deadline:
                try:
                    raw = ws.recv()
                except Exception as exc:
                    timeout_cls = getattr(getattr(websocket, "_exceptions", object()), "WebSocketTimeoutException", ())
                    if timeout_cls and isinstance(exc, timeout_cls):
                        continue
                    transport_error = f"{type(exc).__name__}: {exc}"
                    break
                if not raw:
                    continue
                for payload in _tv_payloads(raw):
                    if payload.startswith("~h~"):
                        ws.send(_tv_frame(payload))
                        continue
                    try:
                        msg = json.loads(payload)
                    except Exception:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    method = str(msg.get("m") or "")
                    params = msg.get("p") or []
                    if not isinstance(params, list):
                        params = []

                    if method in {"du", "timescale_update"} and len(params) >= 2 and isinstance(params[1], dict):
                        payload_obj = params[1].get(series_id)
                        if payload_obj is not None:
                            got = _tv_rows_from_series(payload_obj)
                            if not got.empty:
                                frame = got if frame.empty else pd.concat([frame, got])
                                frame = frame[~frame.index.duplicated(keep="last")].sort_index().tail(bars)
                    elif method == "series_completed" and len(params) >= 2 and str(params[1]) == series_id:
                        completed = True
                    elif method == "symbol_error" and len(params) >= 2 and str(params[1]) == alias:
                        symbol_errors[ticker] = _tv_error_text(method, params)
                        completed = True
                    elif method == "series_error" and len(params) >= 2 and str(params[1]) == series_id:
                        symbol_errors[ticker] = _tv_error_text(method, params)
                        completed = True
                    elif method in {"critical_error", "protocol_error"}:
                        fatal_error = _tv_error_text(method, params)
                        completed = True
                    if completed:
                        break

            try:
                ws.send(_tv_command("remove_series", [chart_session, series_id]))
                ws.send(_tv_command("chart_delete_session", [chart_session]))
            except Exception:
                pass

            if fatal_error:
                raise ExactMarketDataError(f"TradingView protocol fatal error for {ticker}: {fatal_error}")
            if transport_error:
                if not results:
                    raise ExactMarketDataError(f"TradingView websocket receive failed for {ticker}: {transport_error}")
                print(f"[TradingView] partial batch stopped after {len(results)}/{len(stocks)}: {transport_error}")
                break
            if not frame.empty:
                results[ticker] = frame.tail(bars)
            elif ticker not in symbol_errors and not completed:
                symbol_errors[ticker] = f"timeout waiting for series completion ({per_symbol_budget:.1f}s)"

        if symbol_errors:
            sample = "; ".join(f"{k}={v}" for k, v in list(symbol_errors.items())[:4])
            suffix = "" if len(symbol_errors) <= 4 else f"; +{len(symbol_errors)-4} more"
            print(f"[TradingView] symbol/series errors {len(symbol_errors)}/{len(stocks)}: {sample}{suffix}")
        if not results and not symbol_errors:
            raise ExactMarketDataError(f"TradingView returned no candle series for batch of {len(stocks)}")
        return results
    finally:
        try:
            ws.close()
        except Exception:
            pass

def _download_tradingview_group(stocks: list[Any], bars: int, timeout: float) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    failures: list[str] = []
    group_count = max(1, math.ceil(len(stocks) / TV_SERIES_PER_SOCKET))

    for group_no, start in enumerate(range(0, len(stocks), TV_SERIES_PER_SOCKET), 1):
        group = stocks[start:start + TV_SERIES_PER_SOCKET]
        try:
            out.update(fetch_tradingview_us_batch(group, bars=bars, timeout=timeout))
        except Exception as exc:
            tickers = ",".join(str(getattr(x, "ticker", "?")) for x in group[:4])
            if len(group) > 4:
                tickers += ",..."
            detail = f"socket-group {group_no}/{group_count} [{tickers}]: {type(exc).__name__}: {exc}"
            failures.append(detail)
            print(f"[TradingView] {detail}")

        if start + TV_SERIES_PER_SOCKET < len(stocks):
            # Public anonymous websocket endpoints are sensitive to bursts,
            # especially from shared CI IP ranges. Modest pacing dramatically
            # reduces connection churn without changing the source or candles.
            time.sleep(random.uniform(*TV_SOCKET_PAUSE))

    if failures and not out:
        summary = " | ".join(failures[:3])
        if len(failures) > 3:
            summary += f" | +{len(failures)-3} more failed socket groups"
        raise ExactMarketDataError(
            f"all TradingView socket groups failed for {len(stocks)} symbols; {summary}"
        )
    if failures:
        print(
            f"[TradingView] batch partial: priced={len(out)}/{len(stocks)}, "
            f"failed_socket_groups={len(failures)}/{group_count}"
        )
    return out

def exact_source_preflight(stocks: Iterable[Any], category: str, timeout: float = 18.0) -> tuple[int, int]:
    """Fast exact-source connectivity/protocol probe before a large scan.

    KR preferentially probes Samsung Electronics (005930) when present because
    it is a stable Toss product code. US probes up to three actual universe
    symbols. Any shared HTTP/transport/protocol failure is surfaced verbatim.
    """
    stock_list = list(stocks)
    if not stock_list:
        return 0, 0
    cat = str(category).upper()
    if cat in {"KR", "KR_ETF"}:
        preferred = [
            s for s in stock_list
            if re.sub(r"\.(KS|KQ)$", "", str(getattr(s, "symbol", "")).upper()) == "005930"
        ]
        sample = (preferred + [s for s in stock_list if s not in preferred])[:2]
    else:
        sample = stock_list[:3]
    probe_bars = min(TOSS_C_CHART_MAX, 120) if cat in {"KR", "KR_ETF"} else 5
    frames = download_market_frames(sample, cat, bars=probe_bars, timeout=timeout)
    usable = sum(
        1 for stock in sample
        if frames.get(str(stock.ticker)) is not None and not frames[str(stock.ticker)].empty
    )
    if usable <= 0:
        raise ExactMarketDataError(
            f"{cat} exact-source preflight returned 0/{len(sample)} usable symbols"
        )
    return usable, len(sample)


def download_market_frames(stocks: Iterable[Any], category: str, bars: int = 720, timeout: float = 42.0) -> dict[str, pd.DataFrame]:
    stock_list = list(stocks)
    cat = str(category).upper()
    if cat in {"KR", "KR_ETF"}:
        return _download_toss_group(stock_list, bars=bars, timeout=min(timeout, 30.0))
    if cat in {"US", "US_ETF"}:
        return _download_tradingview_group(stock_list, bars=bars, timeout=timeout)
    raise ValueError(f"unsupported category: {category}")
