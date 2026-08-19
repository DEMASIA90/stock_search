# Morning Invest v10.0 — V3 눌림목/돌파 전면 교체

## 전략 매핑

- `싼게 좋아` = 첨부 V3 명세의 **눌림목 매매**
- `오르는게 좋아` = 첨부 V3 명세의 **돌파 매매**
- 두 전략은 별도 리스트/별도 Score로 유지하며 한 종목이 양쪽을 동시에 통과하면 최종 Score가 높은 전략에만 채택(P3).
- 최종 진입 후보는 `Score >= 50`.
- `Score = RawScore × 시장국면 M`.

## ETF 스캔 범위

런타임은 `etf_tickers.json`만 ETF 유니버스의 진실 원본으로 사용합니다.

- KR ETF: 첨부 XLSX `KR_300`의 300개 ticker만
- US ETF: 첨부 XLSX `US_500`의 500개 ticker만
- 유니버스 코드가 정확히 300 / 500 unique인지 workflow와 universe.py가 둘 다 검증
- ETF 가격/지표는 일반 종목과 같은 V3 계산 경로 사용
- 첨부 명세의 U4는 일반 주식 유니버스에서 ETF를 제외하기 위한 규칙이므로, 사용자가 별도로 요청한 KR_ETF / US_ETF 전용 카테고리에서는 ETF 제외 조건만 예외 처리

## 데이터/지표

필요 스트림:
- Adjusted OHLC
- Split-adjusted Volume
- MA20 / MA50 / MA120
- ATR14
- OBV
- OHLCV

현재 무료 데이터 공급원 한계:
- V3 U2는 '거래소 원본 거래대금'을 요구하지만 Yahoo 일봉은 거래소 원본 거래대금 필드를 직접 제공하지 않습니다.
- 구현은 **unadjusted raw Close × raw Volume**으로 20일 평균 거래대금을 계산합니다.
- 수정주가 × 원거래량을 섞는 금지 조합은 사용하지 않습니다.

## 시장국면 benchmark

명세에 benchmark symbol 자체는 지정되어 있지 않아 다음으로 고정했습니다.
- KR / KR ETF: `^KS11` (KOSPI)
- US / US ETF: `^GSPC` (S&P 500)

M:
- Index Close > MA20 and MA20 > MA60 → 1.00
- Index Close > MA20 → 0.85
- else → 0.60

## 백테스트

- t = 마지막 확정 일봉
- t+1 adjusted Open 진입
- X1~X3 실행 필터 적용
- 동일 종목 재신호 cooldown 5 거래일
- 양쪽 동시 통과 시 높은 Score 하나만 채택
- 5/10/20D 절대수익 + 지수 초과수익
- MAE20 / MFE20 / Stop 반영 TradeRet
- UI 요약 승률은 지수 초과수익 기준

현재 데이터 공급원으로는 과거 특정 날짜의 관리/투자주의/거래정지 상태를 완전 재구성하지 못하므로,
historical regulatory status와 survivorship bias는 남아 있습니다.

## UI

기존 두 컬럼 유지:
- 왼쪽 `싼게 좋아`
- 오른쪽 `오르는게 좋아`

카드:
- 종목명
- TICKER
- MARKET
- 시총
- 현재가
- Score (클릭 가능)
- 차트 / 뉴스 / 백테스팅

Score를 누르면 modal:
- RawScore / M / 최종 Score
- S1~S7 또는 S1~S6
- 가중치 / 항목점수 / 기여점수
- Gate PASS/FAIL
- 진단값

## 배포

전략 데이터 schema가 바뀌므로 최초 1회:
`Actions -> Run workflow -> ALL -> FULL`

V3는 확정 일봉 전용이므로 자동 intraday QUICK cron은 제거했습니다.
자동 스케줄:
- KR_GROUP: 평일 16:10 Asia/Seoul, FULL
- US_GROUP: 평일 16:20 America/New_York, FULL

수동 QUICK은 사용할 수 있지만 현재 확정 일봉만 사용하고 직전 backtest를 보존합니다.
