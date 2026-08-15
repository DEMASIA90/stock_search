# Morning Invest

GitHub Pages + GitHub Actions 기반의 일봉 주식 스크리너입니다.

- KR: KOSPI + KOSDAQ 상장법인 목록을 KRX KIND에서 갱신
- US: Nasdaq Trader Symbol Directory에서 상장 종목 목록을 갱신
- ETF, 테스트 종목, 워런트/권리/유닛/우선주 계열은 미국 유니버스에서 제외
- 가격 데이터는 yfinance를 통해 일봉으로 수집
- 5개 신호(볼린저, RSI, 거래량, 반전, MACD)를 100점으로 환산
- 유동성/급락 필터 후 시장별 TOP 20 생성
- KR은 평일 16:10 KST, US는 평일 07:10 KST에 예약 갱신

## Manual refresh

GitHub > Actions > `Morning Invest · Update & Deploy` > Run workflow에서 `ALL`, `KR`, `US` 중 선택합니다.

## Files

- `universe.py`: 전체 종목 목록 생성
- `scanner.py`: 가격 수집, 지표/점수 계산, TOP 20 생성
- `docs/index.html`: 화면 구조
- `docs/styles.css`: UI 스타일
- `docs/app.js`: UI/차트
- `.github/workflows/update-and-deploy.yml`: 자동 갱신 및 Pages 배포
