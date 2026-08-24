# Dongtan Trading Center (DTC) — v14.4 Excel Workbook UI

DTC v14.4는 시장 데이터 원본 정책을 유지하면서 UI를 엑셀형 워크북으로 전면 변경하고, 일봉/주봉 SuperTrend 게이트를 결합합니다.

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
상단은 엑셀 웹 UI를 모사한 제목/리본/수식 입력줄이며, 기능 없는 장식 버튼들로 구성됩니다. `앱 설치` 버튼만 PWA 설치 기능이 있습니다. 하단 Sheet는 `국장 / 국장ETF / 미장 / 미장ETF / Quiz`입니다. 종목 행을 클릭하면 L열에 6개월 차트가 펼쳐집니다.
