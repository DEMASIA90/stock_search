# Morning Invest

Current strategy: **v8.3**

Raw score max: **3.5**. Web display score is normalized to **100.0 points**.

- ① Bollinger %B: 1.0
- ② 200-session upper-band Swing: 0.5
- ③ Monthly PSAR: 0.5 (active current month included)
- ④ Daily Heikin-Ashi reversal: 1.0 (20 bearish days before reversal = full score)
- ⑤ Positive MA60 slope: 0.5

Backtest: maximum past 1 trading year, raw score >= 1.0, next-session open entry, 10-session cooldown.
KR/US equities: KRW 10T+ market cap. US ETFs: market-size filter exempt.
