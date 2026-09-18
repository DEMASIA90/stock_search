from __future__ import annotations

import math
import statistics
from typing import Any, Iterable

from src.portfolio import number


BAND_LABELS = ("최하단", "하단", "중단", "상단", "최상단")


def _series(bars: Iterable[dict[str, Any]], key: str) -> list[float]:
    return [number(row.get(key)) for row in bars if number(row.get(key)) > 0]


def simple_average(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def bollinger_state(
    closes: list[float], period: int = 20, deviations: float = 2.0,
) -> dict[str, float | str | None]:
    """Split the current Bollinger channel into five equal vertical zones."""
    if len(closes) < period:
        return {"label": "데이터 부족", "position": None, "lower": None, "middle": None, "upper": None}
    window = closes[-period:]
    middle = statistics.fmean(window)
    sigma = statistics.pstdev(window)
    lower = middle - deviations * sigma
    upper = middle + deviations * sigma
    width = upper - lower
    position = 0.5 if width <= 0 else (closes[-1] - lower) / width
    index = min(4, max(0, math.floor(position * 5)))
    return {
        "label": BAND_LABELS[index],
        "position": position,
        "lower": lower,
        "middle": middle,
        "upper": upper,
    }


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float | None]:
    true_ranges: list[float] = []
    for index, (high, low, close) in enumerate(zip(highs, lows, closes)):
        previous = closes[index - 1] if index else close
        true_ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
    out: list[float | None] = [None] * len(true_ranges)
    if len(true_ranges) < period:
        return out
    value = statistics.fmean(true_ranges[:period])
    out[period - 1] = value
    for index in range(period, len(true_ranges)):
        value = ((value * (period - 1)) + true_ranges[index]) / period
        out[index] = value
    return out


def supertrend_direction(
    bars: list[dict[str, Any]], period: int, multiplier: float,
) -> str:
    """Return UP/DOWN using Wilder ATR and the standard final-band recursion."""
    if len(bars) < period + 2:
        return "UNKNOWN"
    highs = [number(row.get("high")) for row in bars]
    lows = [number(row.get("low")) for row in bars]
    closes = [number(row.get("close")) for row in bars]
    if not all(value > 0 for value in highs + lows + closes):
        return "UNKNOWN"
    atr = _atr(highs, lows, closes, period)
    final_upper: list[float | None] = [None] * len(bars)
    final_lower: list[float | None] = [None] * len(bars)
    trend_up = True
    for index in range(period - 1, len(bars)):
        atr_value = atr[index]
        if atr_value is None:
            continue
        midpoint = (highs[index] + lows[index]) / 2.0
        basic_upper = midpoint + multiplier * atr_value
        basic_lower = midpoint - multiplier * atr_value
        if index == period - 1:
            final_upper[index] = basic_upper
            final_lower[index] = basic_lower
            trend_up = closes[index] >= midpoint
            continue
        previous_upper = final_upper[index - 1]
        previous_lower = final_lower[index - 1]
        if previous_upper is None or previous_lower is None:
            previous_upper, previous_lower = basic_upper, basic_lower
        final_upper[index] = (
            basic_upper
            if basic_upper < previous_upper or closes[index - 1] > previous_upper
            else previous_upper
        )
        final_lower[index] = (
            basic_lower
            if basic_lower > previous_lower or closes[index - 1] < previous_lower
            else previous_lower
        )
        if closes[index] > previous_upper:
            trend_up = True
        elif closes[index] < previous_lower:
            trend_up = False
    return "UP" if trend_up else "DOWN"


def moving_average_slope(closes: list[float], period: int, lookback: int = 5) -> float | None:
    """Five-session percentage slope of an SMA, normalized by its older value."""
    if len(closes) < period + lookback:
        return None
    newest = statistics.fmean(closes[-period:])
    older = statistics.fmean(closes[-period - lookback:-lookback])
    if older == 0:
        return None
    return (newest / older - 1.0) * 100.0


def technical_snapshot(bars: Iterable[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        [dict(row) for row in bars if number(row.get("close")) > 0],
        key=lambda row: str(row.get("date", "")),
    )
    closes = _series(ordered, "close")
    if not closes:
        return {
            "price": 0.0,
            "change_pct": 0.0,
            "band": "데이터 부족",
            "band_position": None,
            "st_10_3": "UNKNOWN",
            "st_20_4": "UNKNOWN",
            "st_14_3": "UNKNOWN",
            "ma60_slope_pct": None,
            "ma200_slope_pct": None,
            "vs_sma60_pct": None,
            "score": 0,
        }
    latest = closes[-1]
    previous = closes[-2] if len(closes) > 1 else latest
    band = bollinger_state(closes)
    ma60 = simple_average(closes, 60)
    slope60 = moving_average_slope(closes, 60)
    slope200 = moving_average_slope(closes, 200)
    st10 = supertrend_direction(ordered, 10, 3.0)
    st20 = supertrend_direction(ordered, 20, 4.0)
    st14 = supertrend_direction(ordered, 14, 3.0)
    score = (
        (10 if st10 == "UP" else 0)
        + (10 if st20 == "UP" else 0)
        + (5 if slope200 is not None and slope200 > 0 else 0)
        + (5 if slope60 is not None and slope60 > 0 else 0)
    )
    return {
        "price": latest,
        "change_pct": (latest / previous - 1.0) * 100.0 if previous else 0.0,
        "band": band["label"],
        "band_position": band["position"],
        "bollinger_lower": band["lower"],
        "bollinger_middle": band["middle"],
        "bollinger_upper": band["upper"],
        "st_10_3": st10,
        "st_20_4": st20,
        "st_14_3": st14,
        "ma60_slope_pct": slope60,
        "ma200_slope_pct": slope200,
        "vs_sma60_pct": (latest / ma60 - 1.0) * 100.0 if ma60 else None,
        "score": score,
    }


def watchlist_sort_key(item: dict[str, Any]) -> tuple[int, float, float, str]:
    band_rank = {label: index for index, label in enumerate(BAND_LABELS)}
    return (
        band_rank.get(str(item.get("band")), len(BAND_LABELS)),
        -number(item.get("score")),
        number(item.get("band_position"), 999.0),
        str(item.get("code", "")),
    )
