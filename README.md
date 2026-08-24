# DTC.xlsx — v14.4.1 Excel UI

v14.4 스캐너/시장 데이터 정책은 그대로 유지하고, v14.4.1에서 PWA 외형과 Quiz 표시를 Excel형으로 전면 정리했습니다.

## 데이터 원본
- KR / KR_ETF: Toss WTS `c-chart` 일봉 OHLC
- US / US_ETF: TradingView public chart websocket 일봉 OHLC
- 기술지표용 OHLC에 Yahoo fallback 없음

## 의견 로직
- ST_D = 일봉 SuperTrend(14,2)
- ST_W = 주봉 SuperTrend(14,2), 현재 진행 중인 주 포함
- CASE1 = 현재 ST_D 상승 + 현재 ST_D >= 최근 하락→상승 전환 직전 마지막 하락 ST_D
- CASE2 = 현재 ST_W 상승 + 현재 ST_W >= 최근 하락→상승 전환 직전 마지막 하락 ST_W
- CASE1 & CASE2 = 매수
- CASE1만 = 단기 매수
- CASE2만 = 장기 매수
- ST_D, ST_W 모두 하락 = 매도
- 둘 중 하나만 하락 = 매도 고려
- 그 외 = HOLD
- ADX(14,14)는 참고 컬럼이며 의견에는 사용하지 않습니다.

## Backtest
최근 2년 동안 최초 `매수(CASE1&CASE2)`에서 진입하고 최초 `매도(일봉/주봉 모두 하락)`에서 청산합니다. 각 완료 cycle의 구간 최고 High 수익률을 계산하고 그 집합의 중위값을 표시합니다. 주봉은 각 과거 일자의 진행 중 주봉만 사용해 룩어헤드를 막습니다.

## UI
상단은 제공된 데스크톱 Excel 화면과 같은 높이/배치의 녹색 제목줄, 리본 탭, 홈 리본, 수식 입력줄로 구성됩니다. 리본 버튼은 장식용이며 기능이 없습니다. 하단 Sheet는 `국장 / 국장ETF / 미장 / 미장ETF / Quiz`입니다. 종목 행을 클릭해도 행 높이는 변하지 않고 L열 위에 여러 행 높이로 차트가 붙여넣기처럼 표시됩니다. 수식 입력줄에 종목명/티커를 입력하고 Enter를 누르면 모든 Sheet와 시총필터를 넘어서 가장 일치하는 종목으로 이동합니다. A~K 헤더는 클릭할 때 오름/내림차순이 전환됩니다. Quiz는 한 문제/보기 5개이며 캔들과 SuperTrend(14,2)만 표시합니다.
