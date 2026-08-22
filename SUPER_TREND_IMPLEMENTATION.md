# DTC v13.0 Supertrend Strategy

## Strategy
- Indicator: Supertrend only, period 10, multiplier 2.
- `P0`: Supertrend value at the most recent DOWN -> UP transition.
- `P1`: current Supertrend value.
- SELL: current Supertrend direction is DOWN.
- BUY eligibility: current direction is UP and `P1 >= P0`.
- Grade uses current closing price relative to `P0`:
  - Buy S: < 2%
  - Buy A: < 5%
  - Buy B: < 10%
  - Buy C: < 20%
  - otherwise Hold.
- Ranking: Buy S, Buy A, Buy B, Buy C, Hold, Sell; market size descending within a level.

## Backtest
- Window: last two calendar years.
- Start flat.
- Enter at the closing price on the first Buy S day while flat.
- Exit at the closing price on the first Sell day while holding.
- Only completed trades enter the average return.

## Chart
- Last ~6 trading months (126 sessions).
- Heikin-Ashi candles are display-only.
- Supertrend is calculated from standard daily OHLC and is the only strategy indicator.

## Removed
- Forecast PJT 1 module, tests, long-history backtest, workflow controls, and UI.
- Prior Forecast/Gwangju references.
- Legacy Bollinger/volume-profile/candle score computation path.
