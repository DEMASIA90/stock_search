# Band Scout · GitHub Pages Edition

개인 서버를 24시간 켜 두지 않고 운영하는 국장/미장 기술적 조건 스크리너입니다.

## 구조

```text
GitHub Actions (Python)
  └─ scanner.py 실행
       └─ Yahoo Finance 가격 수집
       └─ 5개 기술 신호 계산
       └─ docs/data/market.json 생성

GitHub Pages
  └─ docs/index.html
  └─ docs/app.js
  └─ JSON을 읽어 화면/차트 표시
```

즉, 사용자가 사이트에 들어올 때 Python 서버가 실행되는 구조가 아닙니다. 정해진 시간에 GitHub Actions가 계산해 정적 JSON을 만든 뒤 GitHub Pages가 정적 파일로 서비스합니다.

## 5-Signal Composite Score (100점)

각 항목 최대 20점입니다.

1. **Bollinger Lower proximity**: 하단밴드에 가까울수록 높은 점수. Lower 이하 20점, Lower 위 8%에서 0점.
2. **RSI(14) oversold**: RSI 30 이하 20점. 30~60에서 단계적으로 감소.
3. **Volume expansion**: 현재 거래량 / 20일 평균 거래량. 2배 이상이면 20점.
4. **Short reversal momentum**: 1일 및 3일 수익률이 낙폭 둔화/양전환일수록 점수 증가.
5. **MACD improvement**: Histogram 개선, MACD>Signal, 당일 상향 교차를 가점.

등급:
- A: 85~100
- B: 70~84.9
- C: 55~69.9
- D: 55 미만

이 점수는 투자 성공 확률이 아니라 **정의된 5개 기술조건의 동시 충족 정도**입니다.

## 로컬 데이터 생성 테스트

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
python scanner.py
```

성공하면 `docs/data/market.json`이 생성됩니다.

정적 웹 확인:

```bash
python -m http.server 8000 --directory docs
```

브라우저에서 `http://127.0.0.1:8000`.

## GitHub Pages 배포

1. GitHub에서 새 repository 생성. 예: `band-scout`
2. 이 프로젝트의 **내용물 전체**를 repository 루트에 업로드.
3. 기본 브랜치 이름이 `main`인지 확인.
4. Repository → **Settings → Pages**.
5. **Build and deployment → Source → GitHub Actions** 선택.
6. **Actions** 탭 → `Update market data and deploy Pages` 선택.
7. **Run workflow**를 눌러 첫 실행.
8. 완료 후 Settings → Pages에 표시되는 `https://<아이디>.github.io/band-scout/` 주소로 접속.

이후 평일마다 workflow가 자동 실행됩니다.

### 예약 실행 시간

`.github/workflows/update-and-deploy.yml` 기준:

- 07:10 UTC = 16:10 KST
- 22:10 UTC = 07:10 KST(다음 날)

GitHub schedule은 정확한 초 단위 실행 보장이 아니라 큐 상황에 따라 늦어질 수 있습니다.

## 파일 수정 포인트

### 종목 추가/삭제
`universe.py`

### 점수 알고리즘 변경
`scanner.py`
- `score_bollinger()`
- `score_rsi()`
- `score_volume()`
- `score_reversal()`
- `score_macd()`

### 화면 변경
- `docs/index.html`
- `docs/styles.css`
- `docs/app.js`

## 주의

현재 universe는 대표 국장/미장 종목 목록입니다. 전 KOSPI/KOSDAQ 또는 S&P500/Nasdaq 전체로 확대하려면 universe 자동 갱신 또는 정식 데이터 공급원 연동이 필요합니다.

`yfinance` 기반 시장 데이터는 프로토타이핑에 적합하지만, 상용 서비스는 거래소/증권사/정식 시장 데이터 공급자의 이용약관과 재배포 권한을 확인하세요.
