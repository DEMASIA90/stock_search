# DTC v11.5 · PWA + Android

Dongtan Trading Center stock scanner with Firebase/GitHub deployment, installable PWA, and Android Capacitor wrapper.

## Scanner v11.5

- Current setup score: **0~10**.
- Bollinger proximity: upper band or above = 0, lower band or below = 1, linearly interpolated in between.
- Volume profile: each lookback is always split into **10 equal price zones**.
- Lookbacks: **20 / 40 / 60 / 80 / 100 / 150 / 200 / 300 / 400 trading days**.
- Each lookback contributes `volume in the current-price zone / total volume in all 10 zones`, so each component is 0~1.
- Maximum score = Bollinger 1 + nine profile shares 9 = **10**.
- Backtest: evaluate the latest 200 historical dates with known 60-session outcomes. Historical dates whose setup score is at least today's setup score are comparable events.
- 60-day return = `Close[t+60] / Close[t] - 1`.
- The single highest and single lowest return events are removed. Ranking is by the remaining mean 60-day return, highest first.
- If fewer than 3 comparable events exist, the backtest is marked unavailable and the stock sorts after stocks with a valid trimmed mean.

## UI

- Market tabs: 국장 / 국장 ETF / 미장 / 미장 ETF.
- Default screen shows TOP20 by backtest rank; search can find the remaining eligible names.
- Market-cap filters: 10 / 50 / 100 / 500 / 1000조 이상, default 100조 이상.
- Chart window: approximately 3 trading months (63 sessions).
- PWA can be installed from supported browsers and reads market data live from Firebase.

## Recommended first run

Run GitHub Actions with `ALL + FULL` once after deploying v11.5 so all categories are rebuilt using the new score/backtest model.
