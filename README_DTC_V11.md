# DTC v11.5 Scanner Model

## 1. Current setup score (0~10)

### Bollinger component (0~1)

`score = 1 - clamp(%B, 0, 1)`

- upper band or above: 0
- middle band: 0.5
- lower band or below: 1

### 10-zone volume-profile components (0~1 each)

Lookbacks: `20, 40, 60, 80, 100, 150, 200, 300, 400` trading days.

For every lookback, `[minimum Low, maximum High]` is divided into exactly 10 equal price zones. Daily volume is distributed across zones according to Low~High overlap. The component score is:

`current price zone volume / total volume across all 10 zones`

Nine lookbacks can contribute at most 9 points. Together with Bollinger, the theoretical maximum is exactly 10.

## 2. Backtest and ranking

The scanner looks at the latest 200 historical evaluation dates for which a 60-trading-day forward close is already known. Each historical date is scored using the same 0~10 point-in-time model.

A historical date is a comparable event when:

`historical setup score >= current setup score`

For each comparable event:

`60D return = Close[t+60] / Close[t] - 1`

Before averaging, exactly one highest-return event and one lowest-return event are removed. At least 3 comparable raw events are therefore required.

Final stock ordering is descending by this trimmed 60-day mean. The current 0~10 setup score is the setup-quality indicator and acts as the historical comparison threshold; it is not added to the backtest return.
