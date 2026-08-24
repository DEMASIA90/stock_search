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
TOSS_C_CHART_MAX = 500
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
TV_SERIES_PER_SOCKET = 16
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
    """Fetch one public Toss WTS candle page with bounded transport retries.

    4xx responses are never hidden or blindly retried. In particular, 403/429
    usually means the public endpoint has rejected the current CI request/IP;
    hammering 2,570 symbols only makes that condition worse. 5xx and network
    timeouts receive one short bounded retry because they are commonly transient.
    """
    params: dict[str, Any] = {
        "count": int(min(TOSS_C_CHART_MAX, max(1, count))),
        "session": "all",
        "investMode": "krx",
        "useAdjustedRate": "true",
    }
    if from_datetime:
        params["from"] = from_datetime
    url = f"{TOSS_WTS_BASE}/api/v1/c-chart/kr-s/{product_code}/day:1?{urlencode(params)}"

    last_exc: Exception | None = None
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
                f"{': ' + detail if detail else ''}"
            )
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

        # Some chart responses expose a cursor; prefer it. If absent, the caller
        # derives a cursor from the oldest returned candle.
        cursor = result.get("nextFrom") or result.get("nextBefore") or result.get("nextDateTime")
        return frame, str(cursor) if cursor else None

    if last_exc is not None:
        raise ExactMarketDataError(f"Toss c-chart failed for {product_code}: {last_exc}")
    raise ExactMarketDataError(f"Toss c-chart failed for {product_code}")


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
    """Fetch a small batch of US daily candles from TradingView.

    Protocol notes:
    * ``create_series`` requires distinct series id/key values (``sds_1``, ``s1``).
    * Historical bars can arrive as ``du`` or ``timescale_update`` messages.
    * Symbol-level errors are isolated; connection/protocol failures are raised so
      the scanner can report the real cause instead of silently producing 0/500.
    """
    if websocket is None:
        raise ExactMarketDataError("websocket-client is required for TradingView US candles")
    if not stocks:
        return {}

    chart_session = _tv_session("cs")
    results: dict[str, pd.DataFrame] = {}
    series_to_stock: dict[str, Any] = {}
    alias_to_series: dict[str, str] = {}
    completed: set[str] = set()
    symbol_errors: dict[str, str] = {}
    transport_error: str | None = None
    fatal_error: str | None = None

    ws = _tv_connect(timeout)
    try:
        # A shorter recv timeout lets the deadline loop distinguish a quiet
        # socket from an immediately broken connection.
        try:
            ws.settimeout(min(8.0, max(2.0, float(timeout) / 4.0)))
        except Exception:
            pass

        ws.send(_tv_command("set_auth_token", [TV_AUTH_TOKEN]))
        ws.send(_tv_command("set_locale", ["en", "US"]))
        ws.send(_tv_command("chart_create_session", [chart_session, ""]))
        ws.send(_tv_command("switch_timezone", [chart_session, "exchange"]))

        for i, stock in enumerate(stocks):
            alias, series_id, series_key = _tv_series_spec(i)
            tv_symbol = tradingview_symbol(stock)
            descriptor = "=" + json.dumps(
                {"symbol": tv_symbol, "adjustment": "splits", "session": "regular"},
                separators=(",", ":"),
            )
            ws.send(_tv_command("resolve_symbol", [chart_session, alias, descriptor]))
            # IMPORTANT: series id and series key are distinct protocol fields.
            ws.send(
                _tv_command(
                    "create_series",
                    [chart_session, series_id, series_key, alias, "1D", int(bars)],
                )
            )
            series_to_stock[series_id] = stock
            alias_to_series[alias] = series_id

        deadline = time.monotonic() + timeout
        while len(completed) < len(series_to_stock) and time.monotonic() < deadline:
            try:
                raw = ws.recv()
            except Exception as exc:
                # websocket-client raises WebSocketTimeoutException during quiet
                # periods. Keep waiting until our own deadline in that case.
                timeout_cls = getattr(getattr(websocket, "_exceptions", object()), "WebSocketTimeoutException", ())
                if timeout_cls and isinstance(exc, timeout_cls):
                    continue
                transport_error = f"{type(exc).__name__}: {exc}"
                break
            if not raw:
                continue

            for payload in _tv_payloads(raw):
                if payload.startswith("~h~"):
                    # Echo heartbeat payload in TradingView framing exactly.
                    try:
                        ws.send(_tv_frame(payload))
                    except Exception as exc:
                        transport_error = f"heartbeat send failed: {type(exc).__name__}: {exc}"
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
                    for series_id, series_payload in params[1].items():
                        stock = series_to_stock.get(str(series_id))
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
                    series_id = str(params[1])
                    if series_id in series_to_stock:
                        completed.add(series_id)

                elif method == "symbol_error" and len(params) >= 2:
                    alias = str(params[1])
                    series_id = alias_to_series.get(alias)
                    if series_id:
                        stock = series_to_stock[series_id]
                        symbol_errors[str(stock.ticker)] = _tv_error_text(method, params)
                        completed.add(series_id)

                elif method == "series_error" and len(params) >= 2:
                    series_id = str(params[1])
                    if series_id in series_to_stock:
                        stock = series_to_stock[series_id]
                        symbol_errors[str(stock.ticker)] = _tv_error_text(method, params)
                        completed.add(series_id)

                elif method in {"critical_error", "protocol_error"}:
                    fatal_error = _tv_error_text(method, params)
                    break

            if fatal_error:
                break

        if symbol_errors:
            sample = "; ".join(f"{k}={v}" for k, v in list(symbol_errors.items())[:4])
            suffix = "" if len(symbol_errors) <= 4 else f"; +{len(symbol_errors)-4} more"
            print(f"[TradingView] symbol/series errors {len(symbol_errors)}/{len(stocks)}: {sample}{suffix}")

        if fatal_error:
            raise ExactMarketDataError(f"TradingView protocol fatal error: {fatal_error}")

        if not results and transport_error:
            raise ExactMarketDataError(f"TradingView websocket receive failed before any candles: {transport_error}")

        # A connected socket that receives neither usable bars nor symbol errors
        # is a protocol/edge failure, not 16 independent missing tickers.
        if not results and not symbol_errors:
            unresolved = len(series_to_stock) - len(completed)
            raise ExactMarketDataError(
                f"TradingView returned no candle series for batch of {len(stocks)} "
                f"(completed={len(completed)}, unresolved={unresolved}, timeout={timeout:.0f}s)"
            )

        if transport_error and results:
            print(f"[TradingView] partial socket result {len(results)}/{len(stocks)} before {transport_error}")
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
    frames = download_market_frames(sample, cat, bars=5, timeout=timeout)
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
