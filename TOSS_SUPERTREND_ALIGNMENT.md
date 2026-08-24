# Toss alignment — KR / KR ETF

DTC v14.4은 KR/KR ETF 기술지표 계산에 Toss WTS가 웹 차트에 사용하는 `c-chart` 일봉을 직접 요청합니다.

- path family: `/api/v1/c-chart/kr-s/<productCode>/day:1`
- `useAdjustedRate=true`
- 500봉 제한을 넘는 히스토리는 이전 구간을 추가 요청해 604봉 이상을 확보
- 수신 OHLC를 재보정하지 않고 그대로 ST/ADX 입력으로 사용
- Yahoo OHLC fallback 없음

Toss의 공개 WTS endpoint는 공식 OpenAPI 계약이 아니라 웹사이트 내부 backend이므로 endpoint가 변경되면 DTC는 다른 가격원으로 조용히 대체하지 않고 scan을 실패시킵니다. 공식 Toss OpenAPI의 candle API도 adjusted 옵션을 제공하지만 인증이 필요하므로 공개 PWA/GitHub Actions의 무자격 chart-source로 사용하지 않습니다.
