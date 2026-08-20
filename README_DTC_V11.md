# DTC v11.0

## UI

Header: `동탄 트레이딩 센터 (Dongtan Trading Center, DTC)`

Subtitle: `모든 투자의 책임은 사실 다른 투자자에게 있습니다.`

Browser tab: `Dongtan Trading Center (DTC)`

Each stock card is permanently split into two panes:

- Left: name + ticker, score, sector, market size, current price/day change, one latest-news headline, 60-day backtest expectation.
- Right: one-year price chart with Bollinger Bands and the 40/60/120/200D dominant volume-zone center lines.
- Clicking the score opens the five-part score breakdown without cluttering the card.

## Unified score (100)

### 1. Bollinger lower proximity: 0~20

20-day Bollinger Band, 2 standard deviations.

`%B = (Close - Lower) / (Upper - Lower)`

`BB score = 20 * (1 - clamp(%B, 0, 1))`

Therefore lower band/below = 20, middle band = 10, upper band/above = 0.

### 2~5. Dominant 7-zone volume profile: +20 each

Lookbacks: 40, 60, 120, 200 trading days.

For every lookback:

1. Find the lookback's minimum Low and maximum High.
2. Divide that price range into exactly seven equal price zones.
3. Distribute each daily bar's Volume across every zone overlapped by `[Low, High]`, proportional to overlap length.
4. The zone with the largest accumulated volume is the dominant volume zone.
5. If the current Close is inside that dominant zone, add 20 points; otherwise add 0.
6. The dominant zone's center price is saved and drawn on the chart.

This follows the earlier DTC interpretation of a 7-zone supply/volume profile: the `해당 매물대` is the single largest-volume zone, not merely any one of the seven bins (which would always be true).

## Backtest shown on the card

- Signal: score >= 60 at the close.
- Entry: next trading-day Open.
- Exit: Close 60 trading days after entry.
- Historical signal window: most recent 252 eligible trading days.
- Cooldown: 10 trading days between signals for the same stock.
- Card metric: average 60-day return across those historical signals.
- FULL recalculates the backtest for the displayed TOP100; QUICK refreshes current score and preserves the last FULL backtest.

## Universe rules retained

The old *scoring algorithms* are removed. Existing non-score universe safety rules remain:

- KR restricted/halted/watch-list handling.
- US trading-halt handling.
- minimum history / minimum price checks.
- KR/US equities retain the existing KRW 10T minimum market-size rule.
- ETFs are exempt from the equity 10T rule.

## ETF whitelist

`etf_tickers.json` remains the source of truth:

- KR ETF: exactly 300 unique tickers from the attached workbook.
- US ETF: exactly 500 unique tickers from the attached workbook.
- No ETF outside those lists is added to the scanning universe.

## First deployment

Because the data schema and score model changed, run once with:

`ALL + FULL`

After that, scheduled QUICK/FULL refreshes can continue normally.
