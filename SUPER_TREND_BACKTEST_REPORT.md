# DTC v13.2 SuperTrend Backtest Report

이 파일은 배포 전 정적 설명 파일입니다. 실제 카테고리별 수치는 `ALL + FULL` 또는 각 카테고리 스캔이 완료될 때 다음 경로에 자동 생성됩니다.

- `docs/data/kr/supertrend_backtest_report.md`
- `docs/data/kr-etf/supertrend_backtest_report.md`
- `docs/data/us/supertrend_backtest_report.md`
- `docs/data/us-etf/supertrend_backtest_report.md`

실제 데이터 없이 성과 수치를 임의로 기입하지 않습니다.

## 자동 리포트 항목

- 완료 거래 / 미청산 평가 포함 거래 각각의 거래 수, 승률, 평균, 중앙값, 평균이익, 평균손실, 손익비, 평균 보유봉, 최대이익, 최대손실
- 미청산 거래 수와 평가손익
- 동일 종목 2년 Buy&Hold 평균
- KR: KOSPI200 / US: S&P500 2년 수익률
- 게이트 이후 모든 봉의 S/A/B/C/Hold-OVEREXTENDED +5D/+20D/+60D/매도신호까지 수익률
- 레그당 각 등급 최초 발생 봉만 사용한 중복보정 버전
- `r_at_gate` 히스토그램
- 등급별 `atr_pct` 분포
- 최근 60거래일 S/A/B/C/Hold/매도 분포와 S 표본 이상 경고
- 현재 유니버스 기반 생존편향 및 최근 2년 국면편향 안내

## 체결 규칙

- 상승 레그에서 최초 `매수S` 신호가 확정된 다음 봉 시가 진입
- 같은 상승 레그에서 재진입 금지
- 상승→하락 전환 다음 봉 시가 청산
- 마지막까지 미청산이면 마지막 종가로 별도 평가

## 비용 기본값

- 수수료: 0.015% / side
- 슬리피지: 0.10% / side
- KR 매도세: 0.18%
- US 매도세: 0%
- 환율효과: 미반영
