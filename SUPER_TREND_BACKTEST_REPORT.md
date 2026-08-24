# DTC v14.2 Supertrad Index Backtest Report

백테스트 규칙은 DTC Local v1.14.2와 동일합니다.
- 최초 STRONG BUY 또는 BUY 신호일 종가 진입
- 보유 중 추가 BUY 무시
- 최초 SELL/STRONG SELL 신호일 종가 청산
- 다음 진입 전 ST DOWN 봉 최소 1개 필요
- 완료 cycle의 진입~청산 최고 High 수익률 집합 중위값을 BACKTEST로 표시
- 미완료 cycle은 차트에는 표시하되 중위값에서 제외
