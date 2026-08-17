# Morning Invest v9.0 — Dual Mode

## UI
- App title: **믿건나말거나 주식스캐너**
- Developer label remains Morning Invest.
- Mobile initial stock cards no longer show component score chips. They appear only after expansion.
- Modes:
  - 🧺 **싼게 좋아**: existing v8.7 bottom strategy.
  - 🚀 **오르는게 좋아**: new MA60 breakout/momentum strategy.
- KR / US / US ETF are available in both modes.
- Default screen: mode-specific TOP100.
- Search: searches the complete common summary. In rising mode, a stock that does not satisfy the mandatory breakout rule can still be found and is marked `60일선 돌파 조건 미충족`.

## 오르는게 좋아 scoring (raw 4.5 -> 100 points)
1. **MA60 bullish breakout recency, max 2.0**
   - Current close must remain above MA60.
   - Most recent close crossing from <=MA60 to >MA60 must be within 60 trading sessions.
   - today/1D ago 2.00, 2D 1.75, 3D 1.50, 4D 1.25, 5D 1.00, 6~60D 0.00.
   - The crossover condition itself remains mandatory through 60D, even when recency score has decayed to zero.
2. **Current HA bullishness, max 1.0**
   - daily +0.50
   - current unfinished weekly +0.25
   - current unfinished monthly +0.25
3. **60D lower volume-profile dominance +0.50**
   - Daily-bar approximation: each day's volume is assigned to its typical price `(H+L+C)/3`.
   - If cumulative volume below current price > above current price: +0.50.
4. **Best rise after latest MA60 breakout, max 1.0**
   - `max(high since breakout) / breakout close - 1`.
   - 25% => +0.25, 100% or more => +1.00 cap.
   - Follow-through window capped at 60 trading sessions.

## Backtesting
- Both modes: last max ~1 trading year.
- Signal threshold: normalized score >=50/100.
- Next trading day open entry.
- 10 trading day cooldown.
- 5D / 10D / 20D return, MFE20, MAE20, quality and forecast retained.
- Rising-mode historical unfinished weekly/monthly HA is reconstructed point-in-time.
- Volume profile uses the same daily typical-price proxy point-in-time.

## Deployment
Replace:
- scanner.py
- docs/app.js
- docs/index.html
- docs/styles.css

Then run **ALL** once because both scores and backtests must be regenerated.
The Android WebView APK does not need rebuilding; it will load the updated Firebase web app automatically.
