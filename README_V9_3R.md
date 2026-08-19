# Morning Invest v9.3R — v9.3 strategy restored + fixed ETF whitelist

## Strategy
The stock scoring/backtest logic and the dual-column UI are restored from v9.3.

- 싼게 좋아: original v9.3 scoring/backtest
- 오르는게 좋아: original v9.3 scoring/backtest
- QUICK/FULL behavior: original v9.3 behavior
- V10/V3 pullback/breakout spec logic: removed
- V10 gate/ATR/OBV/RS/supply-profile strategy code: removed
- V10 score-detail modal: removed (UI is the original v9.3 dual-column UI)

## ETF universe
Only this part is retained from the later change:

- KR ETF: exact 300 tickers from the attached workbook
- US ETF: exact 500 tickers from the attached workbook
- `etf_tickers.json` is the source of truth
- No ETF outside the whitelist is sent to the price scanner
- Naver/Nasdaq directory data is used only for names/metadata

## First run
Because the deployed data may currently be V10-shaped, run once with:

`ALL + FULL`

Do not use UI_ONLY for the first restore deployment.
