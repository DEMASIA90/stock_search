# DTC v11.3

> Scanner/backtest component version remains `11.2`; v11.3 adds the Android/Capacitor build layer and deployment fixes without changing the scoring algorithm.

## UI

Header: `동탄 트레이딩 센터 (Dongtan Trading Center, DTC)`

Main market buttons: `국장 / 국장ETF / 미장 / 미장ETF`.

- Normal list: score-ranked TOP20 only.
- Search: searches the full eligible summary universe, including stocks outside TOP20.
- Market-size filters: 10조 / 50조 / 100조 / 500조 / 1000조 이상, default 100조 이상.
- Card chart: most recent ~3 trading months (63 sessions).

## Unified final score (0~100)

The score is built in two stages. Items 1~6 form an 85-point **base score**. Item 7 is a backtest adjustment from -10 to +15. Final display score is clipped to 0~100.

### 1. Bollinger lower-band proximity: 0~10

20-day Bollinger Band, 2 standard deviations.

`%B = (Close - Lower) / (Upper - Lower)`

`BB score = 10 * (1 - clamp(%B, 0, 1))`

- Upper band or above: 0
- Middle band: 5
- Lower band or below: 10
- Between upper/lower: linear interpolation

### 2~6. Dominant 7-zone volume profile

| Lookback | Score when current Close is inside dominant zone |
|---:|---:|
| 20 trading days | +5 |
| 40 trading days | +10 |
| 60 trading days | +15 |
| 120 trading days | +20 |
| 200 trading days | +25 |

For every lookback:

1. Find the lookback minimum Low and maximum High.
2. Divide the price range into exactly seven equal price zones.
3. Distribute each daily bar's Volume across every zone overlapped by `[Low, High]`, proportional to overlap length.
4. The zone with the largest accumulated volume is the dominant volume zone.
5. If the current Close is inside that dominant zone, add the lookback's assigned score. Otherwise add 0.
6. The dominant zone center is saved and drawn on the chart.

`해당 매물대` means the single largest-volume zone among the seven bins.

### 7. Backtest adjustment: -10~+15

To avoid circular scoring, the historical signal is generated from **items 1~6 only**.

Existing execution model is retained:

- Signal: base score >= 60 at the close.
- Entry: next trading-day Open.
- Exit: Close 60 trading days after entry.
- Historical signal window: most recent 252 eligible trading days.
- Cooldown: 10 trading days between signals for the same stock.
- Backtest result used for scoring: average 60-trading-day return.

Adjustment:

| Average return | Score adjustment |
|---:|---:|
| < 0% | -10 |
| 0% to <5% | 0 |
| 5% to <10% | +10 |
| >=10% | +15 |

Therefore the theoretical maximum is `85 + 15 = 100`.

## FULL / QUICK behavior

- **FULL**: recalculates the backtest for every eligible stock because item 7 affects the final rank.
- **QUICK**: refreshes the current 1~6 base score and reuses the latest compatible FULL backtest adjustment.
- If no compatible FULL backtest exists yet, item 7 is neutral (0) until the next FULL run.

## Universe rules retained

- KR restricted/halted/watch-list handling.
- US trading-halt handling.
- Minimum history / minimum price checks.
- KR/US equities retain the existing KRW 10T minimum market-size rule.
- ETFs are exempt from the scanner's hard 10T eligibility rule.
- ETF whitelist remains `etf_tickers.json`: KR 300 and US 500 tickers.

## Deployment

Because the score schema changed, the first deployment should be run once with:

`ALL + FULL`

After that, scheduled QUICK/FULL refreshes can continue normally.
