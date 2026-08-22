# Forecast PJT 1 백테스트 리포트

**상태: 실데이터 백테스트 미실행 — 판정 보류**

이 파일은 배포 ZIP을 만드는 오프라인 검증 환경에서 실제 Yahoo/KRX 5년 데이터를 임의로 생성하지 않기 위해 비워 둔 실데이터 리포트 자리다. 숫자를 조작하거나 합성 데이터 결과를 실전 성과처럼 기재하지 않는다.

실데이터 리포트는 최신 FULL 스캔으로 `docs/data/<cat>/summary.json`이 생성된 뒤 다음 명령으로 작성한다.

```bash
python backtest_forecast.py --download-from-summary docs/data --years 7 --sweep --report forecast_backtest_report.md
```

리포트 생성기는 동일 표본에서 본 모델 / 12-1 모멘텀 / 3개월 수익률 / 감쇠 없음 / 랜덤을 비교하고, 방향 적중률·기준선·엣지·시점별 edge t값·횡단면 IC·ICIR·분위수 스프레드·10분위 단조성·전반/후반 파라미터 스윕을 출력한 뒤 명세의 채택 기준에 따라 최종 판정 문장을 작성한다.
