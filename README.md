# DTC · Supertrend Strategy

DTC의 현재 분석 모드는 **Supertrend(기간 10, 곱 2)** 하나만 사용합니다. Quiz mode는 별도로 유지됩니다.

## 의견 규칙

- `P0`: 가장 최근 Supertrend 하락→상승 전환 시점의 Supertrend 가격
- `P1`: 현재 Supertrend 가격
- 현재 Supertrend가 하락이면 `매도`
- 현재 Supertrend가 상승이고 `P1 >= P0`일 때 현재 종가와 P0의 차이로 등급 결정
  - `<2%`: Buy S
  - `<5%`: Buy A
  - `<10%`: Buy B
  - `<20%`: Buy C
  - 그 외: Hold
- 그 외의 상승 상태도 Hold

정렬은 `Buy S → Buy A → Buy B → Buy C → Hold → 매도`, 동일 의견에서는 시가총액/ETF 규모 내림차순입니다.

## 백테스트

최근 2년 동안 포지션이 없을 때 첫 `Buy S` 종가에 진입하고, 보유 중 첫 `매도` 의견 종가에 청산합니다. 완료된 거래의 평균 수익률을 카드와 상세창에 표시합니다.

## 차트

약 6개월(126 거래일)의 Heikin-Ashi 캔들과 Supertrend(10,2)를 표시합니다. **매매 의견 계산은 일반 OHLC 기준 Supertrend만 사용하며 Heikin-Ashi는 시각화 전용**입니다.

## 테스트

```bash
python -m unittest -v test_supertrend_strategy.py
```
