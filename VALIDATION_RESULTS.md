# Validation results · v14.4.1 Excel UI

- Python compile: PASS
- `node --check docs/app.js`: PASS
- SuperTrend / dual-timeframe tests: 8/8 PASS
- Exact market-data adapter tests: 8/8 PASS
- UI structure checks:
  - title `DTC.xlsx`: PASS
  - opinion column A: PASS
  - formula input editable/searchable: PASS
  - sortable A-K headers: PASS
  - chart overlay without row expansion: PASS
  - Quiz five choices: PASS
  - Quiz Bollinger/volume-profile rendering removed: PASS
  - Quiz SuperTrend(14,2) renderer present: PASS

The scanner/market algorithm is unchanged from v14.4; this revision is a UI/quiz-rendering update, so no new FULL scan is required for the shell itself.
