# Toss Securities / TradingView SuperTrend alignment note

DTC v13.3의 SuperTrend 코어는 TradingView 공개 `pine_supertrend()` reference를 직접 옮긴 상태 머신입니다.

- factor(multiplier): 2.0
- ATR period: 10
- ATR: Wilder RMA
- source: hl2
- final upper/lower band ratchet: TradingView reference와 동일
- direction: DTC 내부 부호만 `+1=UP/-1=DOWN`으로 반대이며 ST line 값에는 영향 없음
- chart: 추세 전환 시 line break
- input: adjusted real OHLC

토스증권 공개 Open API의 일봉 캔들은 `adjusted=true`가 기본값입니다. 따라서 수정주가 사용 원칙도 방향이 같습니다.

다만 공개 Toss 웹 화면/API는 SuperTrend의 계산된 숫자 series 자체를 제공하지 않으므로, 인증된 Toss 캔들 원본 없이 '특정 종목 특정 날짜의 ST 값이 소수점까지 1:1'이라고 증명할 수는 없습니다. DTC는 Yahoo adjusted OHLC를 사용하므로 남는 차이는 주로 데이터 공급원, 기업행사 조정, 장중 마지막 일봉의 구성 차이에서 발생할 수 있습니다.
