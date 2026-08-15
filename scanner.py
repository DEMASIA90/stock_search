from __future__ import annotations

import argparse
import json
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from universe import Stock, get_universe

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

PERIOD = "1y"
CHART_POINTS = 180
BATCH_SIZE = 36
RETRY_BATCH_SIZE = 10
MIN_COVERAGE = 0.20
TOP_N = 20

MARKET_RULES = {
    "KR": {"min_price": 1000.0, "min_turnover": 500_000_000.0},
    "US": {"min_price": 2.0, "min_turnover": 2_000_000.0},
}


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def interp(x: float, points: list[tuple[float, float]]) -> float:
    if not math.isfinite(x):
        return 0.0
    points = sorted(points)
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            ratio = (x - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return 0.0


def finite(value, default=np.nan) -> float:
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except Exception:
        return default


def clean(value, digits=4):
    value = finite(value)
    return round(value, digits) if np.isfinite(value) else None


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = pd.to_numeric(out["Close"], errors="coerce")
    volume = pd.to_numeric(out.get("Volume"), errors="coerce")

    out["SMA20"] = close.rolling(20).mean()
    out["SMA60"] = close.rolling(60).mean()
    std20 = close.rolling(20).std(ddof=0)
    out["Upper"] = out["SMA20"] + 2.0 * std20
    out["Lower"] = out["SMA20"] - 2.0 * std20

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["RSI14"] = 100 - (100 / (1 + rs))
    out["RSI_DELTA3"] = out["RSI14"] - out["RSI14"].shift(3)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["MACD"] = ema12 - ema26
    out["MACDSignal"] = out["MACD"].ewm(span=9, adjust=False).mean()
    out["MACDHist"] = out["MACD"] - out["MACDSignal"]

    out["VolAvg20"] = volume.rolling(20).mean()
    out["Turnover"] = close * volume
    out["TurnoverMed20"] = out["Turnover"].rolling(20).median()
    out["Ret1"] = close.pct_change(1, fill_method=None) * 100
    out["Ret3"] = close.pct_change(3, fill_method=None) * 100
    out["Ret5"] = close.pct_change(5, fill_method=None) * 100

    high = pd.to_numeric(out.get("High"), errors="coerce")
    low = pd.to_numeric(out.get("Low"), errors="coerce")
    open_ = pd.to_numeric(out.get("Open"), errors="coerce")
    day_range = (high - low).replace(0, np.nan)
    out["CLV"] = ((close - low) / day_range).clip(0, 1)
    out["BullCandle"] = (close > open_).astype(float)
    return out


def score_bollinger(gap_pct: float, band_position: float) -> float:
    gap_score = interp(
        gap_pct,
        [(-12, 0), (-8, 5), (-4, 14), (-2, 19), (0, 20), (2, 20), (5, 12), (8, 4), (12, 0)],
    )
    if np.isfinite(band_position) and band_position > 45:
        gap_score *= clamp(1 - (band_position - 45) / 70, 0.55, 1.0)
    return clamp(gap_score, 0, 20)


def score_rsi(rsi: float, delta3: float) -> float:
    base = interp(
        rsi,
        [(5, 2), (15, 7), (22, 15), (28, 20), (35, 19), (40, 16), (45, 10), (50, 4), (55, 0), (70, 0)],
    )
    if np.isfinite(delta3):
        if delta3 >= 5:
            base += 2.5
        elif delta3 >= 2:
            base += 1.5
        elif delta3 <= -6:
            base -= 3.0
        elif delta3 <= -3:
            base -= 1.5
    return clamp(base, 0, 20)


def score_volume(ratio: float, ret1: float) -> float:
    base = interp(ratio, [(0.3, 0), (0.7, 3), (1.0, 8), (1.5, 14), (2.0, 18), (3.0, 20), (5.0, 18)])
    if np.isfinite(ret1):
        if ret1 <= -8:
            base *= 0.35
        elif ret1 <= -4:
            base *= 0.60
        elif ret1 >= 0:
            base += 1.5
    return clamp(base, 0, 20)


def score_reversal(ret1: float, ret3: float, ret5: float, clv: float, bull_candle: float) -> float:
    s = 0.0
    s += interp(ret1, [(-10, 0), (-4, 1), (-1, 3), (0, 5), (2, 7), (5, 6), (10, 2)])
    s += interp(ret3, [(-15, 0), (-6, 1), (-2, 3), (0, 4), (4, 5), (8, 4), (15, 1)])
    s += interp(ret5, [(-25, 0), (-10, 1), (-4, 2), (0, 3), (8, 3), (15, 1)])
    if np.isfinite(clv):
        s += interp(clv, [(0, 0), (0.3, 1), (0.55, 2), (0.75, 4), (1, 5)])
    if bull_candle >= 0.5:
        s += 1.5
    return clamp(s, 0, 20)


def score_macd(row: pd.Series, prev: pd.Series, prev3: pd.Series) -> float:
    macd = finite(row["MACD"])
    signal = finite(row["MACDSignal"])
    hist = finite(row["MACDHist"])
    prev_hist = finite(prev["MACDHist"])
    prev3_hist = finite(prev3["MACDHist"])
    prev_macd = finite(prev["MACD"])
    prev_signal = finite(prev["MACDSignal"])
    if not np.isfinite([macd, signal, hist, prev_hist, prev3_hist, prev_macd, prev_signal]).all():
        return 0.0

    s = 0.0
    if hist > prev_hist:
        s += 6.0
    if hist > prev3_hist:
        s += 4.0
    if prev_macd <= prev_signal and macd > signal:
        s += 7.0
    elif macd > signal:
        s += 4.0
    if hist > 0:
        s += 3.0
    elif hist < 0 and hist > prev_hist:
        s += 1.5
    return clamp(s, 0, 20)


def grade(score: float) -> str:
    if score >= 82:
        return "A"
    if score >= 70:
        return "B"
    if score >= 58:
        return "C"
    return "D"


def analyze(stock: Stock, frame: pd.DataFrame):
    if frame.empty or "Close" not in frame.columns:
        return None
    frame = frame.copy()
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame.dropna(subset=["Close"])
    if len(frame) < 165:
        return None

    ind = add_indicators(frame)
    valid = ind.dropna(subset=["SMA20", "SMA60", "Upper", "Lower", "RSI14", "MACDHist"])
    if len(valid) < 70:
        return None

    row = valid.iloc[-1]
    prev = valid.iloc[-2]
    prev3 = valid.iloc[-4]
    close = finite(row["Close"])
    lower = finite(row["Lower"])
    upper = finite(row["Upper"])
    sma20 = finite(row["SMA20"])
    sma60 = finite(row["SMA60"])
    rsi = finite(row["RSI14"])
    rsi_delta3 = finite(row["RSI_DELTA3"])
    ret1 = finite(row["Ret1"])
    ret3 = finite(row["Ret3"])
    ret5 = finite(row["Ret5"])
    clv = finite(row["CLV"])
    bull_candle = finite(row["BullCandle"], 0.0)
    volume = finite(row.get("Volume"))
    vol_avg20 = finite(row["VolAvg20"])
    vol_ratio = volume / vol_avg20 if vol_avg20 > 0 else np.nan
    turnover20 = finite(row["TurnoverMed20"])
    gap_pct = (close - lower) / lower * 100 if lower > 0 else np.nan
    band_pos = (close - lower) / (upper - lower) * 100 if upper > lower else np.nan
    trend60 = (close - sma60) / sma60 * 100 if sma60 > 0 else np.nan

    rules = MARKET_RULES[stock.market]
    liquid = close >= rules["min_price"] and turnover20 >= rules["min_turnover"]
    crash = (np.isfinite(ret1) and ret1 <= -12) or (np.isfinite(ret5) and ret5 <= -25)
    eligible = bool(liquid and not crash and np.isfinite(gap_pct) and np.isfinite(rsi))

    s_bb = score_bollinger(gap_pct, band_pos)
    s_rsi = score_rsi(rsi, rsi_delta3)
    s_volume = score_volume(vol_ratio, ret1)
    s_reversal = score_reversal(ret1, ret3, ret5, clv, bull_candle)
    s_macd = score_macd(row, prev, prev3)

    total = s_bb + s_rsi + s_volume + s_reversal + s_macd
    if np.isfinite(trend60) and trend60 < -25:
        total -= 6
    elif np.isfinite(trend60) and trend60 < -15:
        total -= 3
    if np.isfinite(gap_pct) and gap_pct < -8:
        total -= 4
    total = round(clamp(total, 0, 100), 1)

    chart = []
    for idx, r in valid.tail(CHART_POINTS).iterrows():
        chart.append(
            {
                "date": pd.Timestamp(idx).date().isoformat(),
                "close": clean(r["Close"]),
                "sma20": clean(r["SMA20"]),
                "upper": clean(r["Upper"]),
                "lower": clean(r["Lower"]),
            }
        )

    return {
        "ticker": stock.ticker,
        "symbol": stock.symbol,
        "name": stock.name,
        "exchange": stock.exchange,
        "market": stock.market,
        "currency": stock.currency,
        "date": pd.Timestamp(row.name).date().isoformat(),
        "close": clean(close),
        "day_change_pct": clean(ret1, 2),
        "ret3_pct": clean(ret3, 2),
        "ret5_pct": clean(ret5, 2),
        "lower": clean(lower),
        "sma20": clean(sma20),
        "sma60": clean(sma60),
        "upper": clean(upper),
        "gap_pct": clean(gap_pct, 2),
        "band_position_pct": clean(band_pos, 1),
        "rsi14": clean(rsi, 1),
        "rsi_delta3": clean(rsi_delta3, 1),
        "volume_ratio": clean(vol_ratio, 2),
        "turnover20": clean(turnover20, 0),
        "trend60_pct": clean(trend60, 1),
        "eligible": eligible,
        "score": total,
        "grade": grade(total),
        "scores": {
            "bollinger": round(s_bb, 1),
            "rsi": round(s_rsi, 1),
            "volume": round(s_volume, 1),
            "reversal": round(s_reversal, 1),
            "macd": round(s_macd, 1),
        },
        "chart": chart,
    }


def frame_for(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        for level in range(raw.columns.nlevels):
            values = set(map(str, raw.columns.get_level_values(level)))
            if ticker in values:
                try:
                    return raw.xs(ticker, axis=1, level=level).copy()
                except Exception:
                    pass
        return pd.DataFrame()
    return raw.copy()


def download_batch(tickers: list[str], timeout=35) -> pd.DataFrame:
    return yf.download(
        tickers=tickers,
        period=PERIOD,
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
        timeout=timeout,
        multi_level_index=True,
    )


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def scan_market(market: str):
    universe, universe_source = get_universe(market)
    by_ticker = {s.ticker: s for s in universe}
    results: dict[str, dict] = {}
    missing: list[str] = []

    all_batches = list(chunks(universe, BATCH_SIZE))
    for batch_no, batch in enumerate(all_batches, 1):
        tickers = [s.ticker for s in batch]
        try:
            raw = download_batch(tickers)
        except Exception as exc:
            print(f"batch {batch_no}/{len(all_batches)} failed: {exc}")
            missing.extend(tickers)
            time.sleep(2.0)
            continue

        for stock in batch:
            try:
                frame = frame_for(raw, stock.ticker)
                item = analyze(stock, frame)
                if item is None:
                    missing.append(stock.ticker)
                else:
                    results[stock.ticker] = item
            except Exception:
                missing.append(stock.ticker)

        if batch_no % 10 == 0 or batch_no == len(all_batches):
            print(f"{market}: {batch_no}/{len(all_batches)} batches, {len(results)} priced")
        time.sleep(random.uniform(0.25, 0.55))

    retry_tickers = [t for t in dict.fromkeys(missing) if t not in results]
    if retry_tickers:
        print(f"{market}: retrying {len(retry_tickers)} missing tickers")
        for batch in chunks(retry_tickers, RETRY_BATCH_SIZE):
            try:
                raw = download_batch(batch, timeout=45)
            except Exception:
                time.sleep(1.5)
                continue
            for ticker in batch:
                if ticker in results:
                    continue
                try:
                    item = analyze(by_ticker[ticker], frame_for(raw, ticker))
                    if item is not None:
                        results[ticker] = item
                except Exception:
                    pass
            time.sleep(random.uniform(0.55, 0.95))

    priced = list(results.values())
    coverage = len(priced) / max(1, len(universe))
    if len(priced) < 100 or coverage < MIN_COVERAGE:
        raise RuntimeError(
            f"{market} price coverage too low: {len(priced)}/{len(universe)} ({coverage:.1%}). Existing site data was not overwritten."
        )

    eligible = [x for x in priced if x["eligible"]]
    focus = [x for x in eligible if x["gap_pct"] is not None and -10 <= x["gap_pct"] <= 12 and (x["rsi14"] or 100) <= 60]
    pool = focus if len(focus) >= TOP_N else eligible
    pool.sort(key=lambda x: (-x["score"], abs(x["gap_pct"]) if x["gap_pct"] is not None else 999, x["symbol"]))
    top = pool[:TOP_N]
    for rank, item in enumerate(top, 1):
        item["rank"] = rank

    market_date = max((x["date"] for x in priced if x.get("date")), default=None)
    payload = {
        "app": "Morning Invest",
        "market": market,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market_date": market_date,
        "universe_source": universe_source,
        "universe_count": len(universe),
        "priced_count": len(priced),
        "coverage_pct": round(coverage * 100, 1),
        "eligible_count": len(eligible),
        "top_count": len(top),
        "top20": top,
    }
    out = DATA_DIR / f"{market.lower()}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out}: universe={len(universe)}, priced={len(priced)}, eligible={len(eligible)}, top={len(top)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["KR", "US", "ALL"], default="ALL")
    args = parser.parse_args()
    markets = ["KR", "US"] if args.market == "ALL" else [args.market]
    failures = []
    for market in markets:
        try:
            scan_market(market)
        except Exception as exc:
            failures.append((market, str(exc)))
            print(f"ERROR {market}: {exc}")
    if failures and len(failures) == len(markets):
        raise SystemExit(1)
    if failures:
        print("Partial refresh completed; failed markets:", failures)


if __name__ == "__main__":
    main()
