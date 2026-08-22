# Forecast PJT 1 구현 메모

## 프로덕션 모드

PWA에는 **Forecast PJT 1**과 **Quiz mode**만 노출한다. 기존 매물대 현재 셋업 점수 코드는 `scanner.py`에 그대로 남겨 별도 필드로 계산하지만 UI 랭킹에는 사용하지 않는다.

Forecast PJT 1 랭킹은 `forecast.score = r_pred_20d × confidence` 내림차순이다.

## 실행

현재 Forecast/Quiz 데이터 갱신:

```bash
python scanner.py --market ALL --scan-mode FULL
```

5년 이상 권장 연구 백테스트(현재 scanner summary에 포함된 종목만 7년 재다운로드):

```bash
python backtest_forecast.py \
  --download-from-summary docs/data \
  --years 7 \
  --sweep \
  --report forecast_backtest_report.md
```

GitHub Actions 수동 실행에서는 `run_forecast_backtest=true`를 선택하면 같은 장기 백테스트를 별도 artifact로 저장한다. 일반 QUICK/FULL 및 예약 실행에서는 장기 연구 백테스트를 돌리지 않아 배포 시간을 늘리지 않는다.

## 결측 / 0거래량 / 거래정지 처리

- 조정종가가 유효하면 `Adj Close`, 해당 행만 비어 있으면 `Close`를 사용한다.
- 상대거래량 SMA20은 해당일 포함 20거래일의 단순평균이다.
- 앵커 후보는 가격>0, 거래량>0, 상대거래량 유효인 날만 인정한다.
- 한 버킷에 유효일이 5일 미만이면 `forecastable=false`.
- 272거래일 미만도 `forecastable=false`.
- 거래정지로 0거래량이 길게 이어지면 해당 일자는 앵커 후보에서 제외된다.
- 동일 상대거래량 최대값은 시간상 먼저 등장한 날을 선택해 결정론성을 유지한다.

## 명세 일관성 메모 — 알고리즘은 임의 수정하지 않음

명세 §2.3은 마지막 버킷을 `τ∈[-41,0]`으로 정의해 **오늘(τ=0)이 상대거래량 최대면 오늘 종가가 앵커로 적합에 들어갈 수 있다.** 반면 §2.7 설명에는 “적합에 오늘 종가는 들어가지 않았으므로 진짜 out-of-sample 잔차”라고 적혀 있다. 두 문장은 동시에 항상 참일 수 없다.

이번 구현은 요청대로 **§2.3의 버킷 정의를 문자 그대로 유지**했다. 따라서 JSON에 `today_is_anchor`를 추가해 이 상황을 확인할 수 있게 했으며, 알고리즘 자체는 임의 변경하지 않았다. 오늘을 앵커 후보에서 강제로 제외하려면 별도 명세 변경이 필요하다.
