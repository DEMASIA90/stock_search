Morning Invest v9.2 — KR ETF bootstrap hotfix

Fixes
1. KR ETF universe no longer dies on the first KRX HTTP 400.
   - Primary: KRX Data Marketplace, with browser-like session + fuller form fields.
   - Operational fallback: Naver Finance ETF item list (codes/names only).
   - Final fallback: hydrated/local KR ETF universe cache.
   - Price history and strategy scoring remain on the existing Yahoo path.

2. Yahoo permanent-missing retry loop is shortened.
   - If a complete retry recovers zero tickers and the missing set is unchanged,
     repeated retries stop immediately.
   - The >=95% market price coverage gate is still enforced.

Replace together
- universe.py
- scanner.py
- hydrate_data.py
- .github/workflows/update-and-deploy.yml

Then run
Actions -> Morning Invest -> ALL -> FULL

Expected KR ETF bootstrap log
KRX ETF master unavailable: ...       # acceptable if KRX still rejects automation
KR ETF universe: NAVER fallback (...) # acceptable operational fallback
Morning Invest | KR_ETF | mode=FULL | universe=...

The old KQ 'possibly delisted' warnings may still appear once; they should no
longer be retried repeatedly when the same set makes no progress.
