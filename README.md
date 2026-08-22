# DTC · Forecast PJT 1

DTC PWA는 두 개의 모드만 노출합니다.

- **forecast pjt 1** — 상대거래량 앵커 6개 + 최근성 감쇠 WLS로 향후 20거래일 방향/상대순위를 예측
- **Quiz mode** — 기존 차트 재구성 퀴즈

## Forecast PJT 1

프로덕션 예측 함수는 `trend_forecast.py::forecast()`에 완전히 분리되어 있습니다.

```python
forecast(df, half_life=84, n_buckets=6, horizon=20)
```

주요 규칙:

- 조정종가 사용
- 최소 272거래일
- 최근 252일을 기본 6구간(42일씩)으로 분리
- 각 구간 상대거래량 `Volume / SMA20(Volume)` 최대일을 앵커로 선택
- `rv × exp(-ln(2)|tau|/H)` 가중치, 최대/최소 비율 4배 cap
- 로그종가 WLS 1차 적합
- 20D 예측수익률 ±20% clip
- `confidence = conf_t × conf_z`
- `score = r_pred_20d × confidence`
- scanner/UI 랭킹은 forecast score 내림차순

기존 매물대 현재 셋업 점수 코드는 `scanner.py`에 별도 필드로 유지하지만 UI 모드와 랭킹에는 사용하지 않습니다.

## 테스트

```bash
python -m unittest -v test_trend_forecast.py
```

필수 검증: 기울기 복원, 앵커 선택, 반감기 가중치 작동, 룩어헤드 차단, 실제 P0 레벨 출발, 가중치 4배 cap. 추가로 고속 백테스트 엔진과 프로덕션 score의 일치도 검증합니다.

## 연구 백테스트

정상 FULL 스캔으로 `docs/data/*/summary.json`을 만든 뒤:

```bash
python backtest_forecast.py \
  --download-from-summary docs/data \
  --years 7 \
  --sweep \
  --report forecast_backtest_report.md
```

리포트는 시장조정 20D 수익률을 기준으로 방향 적중률/기준선/엣지, 시점별 edge t값, 횡단면 IC/ICIR, 분위수 스프레드, 10분위 단조성, 12-1/3M/no-decay/random 베이스라인, H×N×h 전반/후반 스윕을 생성합니다.

GitHub Actions 수동 실행의 `run_forecast_backtest=true`도 같은 리포트를 artifact로 생성합니다.

## 배포

Firebase Hosting 변수:

- `FIREBASE_PROJECT_ID=dtc-lab`
- `FIREBASE_SITE_ID=dtc-lab`
- Secret: `FIREBASE_SERVICE_ACCOUNT_JSON`

일반 배포는 `.github/workflows/update-and-deploy.yml` 하나만 사용합니다.
