# DTC v11.8 · PWA + Android

Dongtan Trading Center stock scanner with Firebase/GitHub deployment, installable PWA, Android Capacitor wrapper, and chart-reconstruction Quiz mode.
aa
## Scanner v11.8

- Current setup score: **0~10** and ranking is by this current setup score only.
- Bollinger proximity: upper band or above = 0, lower band or below = 1, linearly interpolated in between.
- Volume profile: each lookback is split into **10 equal price zones**.
- Lookbacks are grouped to reduce duplicated information:
  - Short: 20 / 40 / 60 trading days
  - Medium: 80 / 100 / 150 trading days
  - Long: 200 / 300 / 400 trading days
- Each lookback keeps the raw current-zone volume share, but scoring uses `current-zone share / largest-zone share`.
- Each horizon group contributes `3 × mean(normalized lookback values)`, so Bollinger 1 + short 3 + medium 3 + long 3 = **10**.
- Historical 60-session results are **reference-only** and never determine rank.
- Historical samples are spaced by 60 trading sessions within each stock to avoid overlapping 60-day return labels, then pooled across the current market category.
- 60-day historical return uses `Adj Close` when available so dividends are reflected.
- The pooled reference expands the score band from ±0.5 up to ±2.0 only when more samples are needed.
- Historical delisted constituents are not available from the free current-universe data source; this survivorship limitation is recorded in output metadata and is one reason the reference backtest is not used for ranking.

## Quiz mode

- Quiz universe: stocks/ETFs with market cap or AUM of at least **KRW 100 trillion**.
- One question displays a random **90-trading-day** candlestick chart with Bollinger Bands and a 10-zone volume profile.
- One randomly selected 30-day third of the chart is hidden.
- Four answer charts show possible close-price shapes.
- The correct answer is the real hidden close series.
- Distractors are based on real 30-day market patterns and volatility-matched. Middle-third questions bridge both edges; first/last-third questions anchor only the visible-side edge so the unknown endpoint is not leaked.
- Quiz data is stored as lightweight manifests plus lazy-loaded per-stock OHLCV JSON files. The browser only downloads the few stock histories needed for the current question and reuses them in an in-memory cache.

## UI

- Modes: 매물대 분석 / 캔들 분석 / Quiz.
- Market tabs: 국장 / 국장 ETF / 미장 / 미장 ETF.
- Equity market-cap filters: 10 / 50 / 100 / 500 / 1000조 이상, default 100조 이상.
- ETF size filters: 전체 / 0.1 / 0.5 / 1 / 5조 이상.
- Card signals: pullback = Bollinger %B + RSI(14); breakout = distance vs prior 20-day high + current volume / prior 20-day average volume.
- RSI warm-up remains NaN until enough observations exist.

## Reliability changes

- Korean equities use a best-effort KRX bulk market-cap snapshot before Yahoo per-symbol fallback.
- Korean ETFs use Naver ETF market size as the bulk primary source.
- Yahoo size lookup retries apply exponential backoff even when metadata returns `None` instead of raising.
- US ETF whitelist cache no longer overwrites the full Nasdaq Trader ETF fallback cache.
- USD/KRW records its source; FULL scans refuse the fixed 1400 fallback when live/recent cached FX is unavailable.
- Category files are published atomically with `summary.json` replaced last so clients do not see references to incomplete detail files.
- Every successful workflow uploads a 14-day `docs/data` snapshot artifact as a second copy of generated market data.

## Recommended first run

After deploying v11.8, run GitHub Actions with **`ALL + FULL` once**. This rebuilds all four market categories, the new score model, pooled backtest references, and all Quiz manifests/details.
