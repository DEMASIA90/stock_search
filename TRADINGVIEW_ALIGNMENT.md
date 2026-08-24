# TradingView alignment — US / US ETF

DTC v14.4은 US/US ETF 기술지표 계산에 TradingView public/anonymous chart websocket에서 직접 받은 일봉을 사용합니다.

- interval: `1D`
- session: `regular`
- adjustment: `splits`
- timezone: exchange
- ST: TradingView `ta.supertrend(2,14)` 계산 의미와 일치하도록 구현
- ADX: TradingView/Pine DMI `14,14` RMA 계산 의미와 일치하도록 구현
- Yahoo OHLC fallback 없음

카드 클릭으로 띄우는 DTC TradingView Advanced Chart도 public/default chart를 사용하므로 scanner의 목표 feed와 동일한 범주입니다.

주의: 사용자가 별도 TradingView 로그인 계정에서 유료 primary-exchange realtime data를 활성화한 경우, 그 차트는 public/default TradingView US feed와 다른 원시 데이터가 될 수 있습니다. 서버가 사용자의 private market-data entitlement를 사용할 수 없으므로 그 경우까지 동일성을 보장할 수는 없습니다.
