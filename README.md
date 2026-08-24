# Dongtan Trading Center (DTC) — v14.2 Supertrad Index

DTC Local **v1.14.2 PrevDownSTGate** 규칙을 웹/PWA 스캐너에 이식한 버전입니다.

## 현재 알고리즘
- SuperTrend: 14,2
- ADX: DI 14 / smoothing 14
- ADX >= 70: STRONG SELL
- 40 <= ADX < 70: SELL
- ST 상승 + 25 <= ADX < 30: BUY
- ST 상승 + 20 <= ADX < 25: STRONG BUY 후보
- STRONG BUY gate의 P0는 **하락→상승 전환 직전 마지막 DOWN ST 값**
- 전환봉 자체(age 0)는 STRONG BUY 제외
- 다음 UP 봉부터 현재 ST >= P0일 때 STRONG BUY
- 그 외 HOLD

백테스트는 최근 2년 BUY→SELL cycle 최고수익률 중위값을 사용합니다.

## 차트
- DTC 자체 6개월 일반 일봉 + ST(14,2) + ADX(14,14)
- 차트 클릭 시 TradingView가 아니라 **토스증권 WTS 종목 차트**를 새 탭으로 엽니다.
- 국내 종목은 Toss product code를 직접 구성합니다.
- 미국 종목은 스캔 시 Toss WTS 검색 결과를 캐시하여 가능한 경우 종목 페이지로 바로 연결합니다. 코드가 아직 없는 종목은 토스증권 홈을 열고 티커를 클립보드에 복사합니다.

## Quiz
한 세션은 **5문제**입니다. 5번째 제출 후 정답 수를 표시하고 새 5문제 세션을 시작할 수 있습니다.


## TradingView alignment
ST/ADX calculations use Pine-compatible RMA/TR/DMI initialization and raw chart OHLC. See `TRADINGVIEW_ALIGNMENT.md`.
