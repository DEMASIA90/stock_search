# DTC v13.2 SuperTrend Validation

로컬 정적/합성 검증 결과:

1. Wilder RMA의 최초 seed가 SMA(10), 이후 `(prev*9+TR)/10` 재귀식과 일치 — PASS
2. 별도로 작성한 §2 수식 참조 구현과 ATR/final bands/direction/ST 전체 배열 일치 — PASS
3. 합성 하락→상승 전환에서 `P0 == ST[flip_idx-1]` 및 `P0 > ST[flip_idx]` — PASS
4. 게이트 통과 표본에서 `Close >= P1 >= P0`, `r_pct >= 0` — PASS
5. 한 상승 레그에서 게이트가 한 번 통과하면 레그 종료까지 다시 false가 되지 않음 — PASS
6. S/A/B/C 임계값이 if/elif 배타구간으로 동작 — PASS
7. 차트 payload가 Heikin-Ashi가 아닌 실제 adjusted OHLC와 일치 — PASS
8. 2년 백테스트 동일 입력 반복 시 동일 출력 — PASS
9. 최근 일별 의견 진단 배열이 최대 60거래일로 제한됨 — PASS

실제 TradingView 화면과의 KR 2종목/US 1종목 수동 대조는 이 오프라인 빌드 환경에서 수행했다고 주장하지 않습니다. 첫 FULL 실행 후 동일 종목의 `ST(10,2)`와 flip 날짜를 TradingView에서 대조할 수 있도록 계산식/ATR RMA/초기화가 고정되어 있습니다.
