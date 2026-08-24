# TradingView/Pine alignment — DTC v14.2

DTC indicator math now mirrors TradingView/Pine semantics:

- SuperTrend(14,2): TradingView published band ratchet; ATR = `ta.rma(ta.tr(true), 14)`.
- ADX(14,14): Pine DMI sequence using `ta.change`, `ta.tr`, `ta.rma`, and `fixnan`.
- Price basis: raw chart OHLC (`yfinance auto_adjust=False`); no `Adj Close / Close` dividend rescaling.
- Missing bars are omitted, never forward-filled.

The formulas and initialization are TradingView-compatible. Exact live values can still differ if Yahoo and TradingView have different current-bar OHLC, exchange consolidation, session handling, or corporate-action histories. For closed daily bars, this removes the prior deterministic formula/adjustment mismatch.
