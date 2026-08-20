# Dongtan Trading Center (DTC)

Static stock scanner deployed from GitHub Actions to Firebase Hosting.

## Current scanner

- Final score: 0~100. Items 1~6 create an 85-point base score and the backtest adds -10/0/+10/+15.
- Bollinger lower-band proximity: 0~10 points, linearly scored from upper band (0) to lower band (10).
- Dominant 7-zone volume profiles: 20D +5, 40D +10, 60D +15, 120D +20, 200D +25 when the current close is inside the dominant zone.
- Backtest: base score >=60, next-session open entry, 60 trading-day close exit; average return adds -10/0/+10/+15.
- Main list: TOP20 only; search covers the full eligible summary universe.
- Market-cap filters: 10/50/100/500/1000조 이상, default 100조 이상.
- Card chart: latest 63 trading sessions (about 3 months).
- ETF universe is restricted to the user-supplied whitelist in `etf_tickers.json` (KR 300 / US 500).

See `README_DTC_V11.md` for implementation details.

## Android app (v11.3)

Android APK/AAB 빌드 구성이 포함되어 있습니다. 자세한 사용법은 `README_ANDROID.md`를 참고하세요.
GitHub Actions의 **DTC Android · Build APK & AAB** workflow를 실행하면 install 가능한 debug APK가 생성됩니다.
