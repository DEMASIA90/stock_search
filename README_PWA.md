# DTC PWA v13.3.1

정적 자산 build key는 `20260823-v13_3_1_tvchart`이며 서비스워커 캐시는 `dtc-pwa-v13-3-1-tvchart`입니다.

SuperTrend 화면은 일반 OHLC 126거래일 차트와 ST(10,2)를 사용합니다. 양봉/상승 ST는 빨강, 음봉/하락 ST는 파랑입니다. Quiz mode는 기존 로직을 유지합니다.


## TradingView 인터랙티브 차트

종목 카드의 DTC 차트를 클릭하거나 키보드 Enter/Space로 선택하면 TradingView Advanced Chart가 모달로 열립니다. 카드 자체의 6개월 SuperTrend 차트는 빠른 스캔용으로 유지되며, TradingView 위젯은 클릭할 때만 외부에서 로드합니다. TradingView 쪽 Supertrend를 비교하려면 위젯의 지표 메뉴에서 ATR Length 10, Factor 2를 설정합니다.
