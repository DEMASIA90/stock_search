# Dongtan Trading Center (DTC)

Static stock scanner deployed from GitHub Actions to Firebase Hosting.

## Current scanner

- One unified 100-point score: 85-point technical base score plus a -10~+15 backtest adjustment.
- Bollinger lower-band proximity: 0~20 points.
- 40/60/120/200 trading-day 7-zone volume profiles: +20 points each when the current close is inside that lookback's dominant (highest-volume) zone.
- One-year chart: price + Bollinger Bands + the four dominant volume-zone center lines.
- Backtest: historical items 1~6 base score >=60, next-session open entry, 60 trading-day close exit; average return adds -10/0/+10/+15.
- ETF universe is restricted to the user-supplied whitelist in `etf_tickers.json` (KR 300 / US 500).

See `README_DTC_V11.md` for implementation details.
