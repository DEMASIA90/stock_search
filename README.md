# Dongtan Trading Center (DTC) — v13.3 SuperTrend

DTC v13.3은 `SuperTrend(10,2)` 단독 Opinion 엔진과 Quiz mode를 제공합니다.

## Opinion

- **강한 매수**: 현재 ST 상승 + `P1 >= P0`, 그리고 최초 게이트 통과일을 0으로 하여 `bars_since_gate <= 3`
- **매수**: 현재 ST 상승 + `P1 >= P0`, `bars_since_gate > 3`
- **Hold**: 상승 상태지만 P0가 없거나 아직 `P1 < P0`
- **매도**: 현재 ST 하락

`P0 = ST[flip_idx-1]`, `P1 = current ST`입니다. 기본 정렬은 `강한 매수 → 매수 → Hold → 매도`, 같은 의견에서는 시가총액/ETF 규모 내림차순입니다.

## SuperTrend 엔진

- ATR period 10
- multiplier 2.0
- Wilder RMA
- 수정 OHLC
- TradingView `ta.supertrend(2,10)`의 공개 Pine reference state machine을 직접 옮긴 구현
- 내부 direction 부호만 `+1=상승`, `-1=하락`으로 사용
- 차트는 최근 126거래일 일반 OHLC 캔들: 양봉 빨강, 음봉 파랑, ST 상승 빨강, ST 하락 파랑

## Backtest

최근 2년 각 상승 레그의 최초 **강한 매수** 신호 다음 봉 시가에 진입했다고 가정합니다. 다음 매도 신호 전까지 일중 고가가 진입가 대비 **+10% 이상 한 번이라도 도달한 비율**만 백테스트 headline으로 표시합니다. 미청산 레그는 승률 분모에서 제외합니다.

## 참고 태그

카드의 시총/현재가 아래에 다음 두 값이 `좋음 / 보통 / 나쁨`으로 표시됩니다. 이 값들은 Opinion, 정렬, 백테스트에 절대 반영되지 않습니다.

- 돌파매매: 직전 20일 고점과 당일 거래량/직전20일 평균거래량
- 눌림목 매매: ST 상승 여부 + EMA20/EMA50 + RSI14 + EMA20 이격

## 실행

```bash
python scanner.py --market ALL --scan-mode FULL
python test_supertrend_strategy.py
```

최초 배포 후에는 `ALL + FULL` 1회를 권장합니다.
