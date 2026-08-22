# DTC v13.3 SuperTrend Validation

- Wilder RMA seed/recurrence 테스트
- TradingView 공개 `pine_supertrend()` reference와 ATR/final bands/direction/ST 전 배열 일치 테스트
- P0 = ST[flip-1] 테스트
- 게이트 불변식 및 래치 테스트
- 게이트 0~3봉 강한 매수 / 4봉 이후 매수 테스트
- 일반 OHLC 차트 payload 테스트
- 참고용 돌파/눌림 태그 테스트
- +10% 도달 백테스트 결정론 테스트

`python test_supertrend_strategy.py` 기준 8개 테스트를 실행합니다.
