# DTC v14.1 Supertrad Index 구현

기준: 첨부 DTC Local v1.14.2 PrevDownSTGate.

STRONG BUY gate:
1. ST_DIR -1 → +1 flip을 찾는다.
2. P0 = flip 직전 봉의 DOWN SuperTrend 값 ST[i-1].
3. flip 봉 자체는 age=0으로 제외한다.
4. 다음 UP 봉부터 current ST >= P0 이고 20 <= ADX < 25이면 STRONG BUY.

BUY / SELL / STRONG SELL / HOLD 및 2년 BUY→SELL cycle 백테스트는 로컬 v1.14.2와 동일하다.
