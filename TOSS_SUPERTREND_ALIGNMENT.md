# Toss Securities chart integration

DTC v14.2의 자체 계산 차트는 SuperTrend(14,2) + ADX(14,14)를 표시합니다. 카드 차트를 클릭하면 토스증권 WTS의 해당 종목 페이지를 새 탭으로 엽니다.

- KR/KR ETF: `A` + 6자리 종목코드로 직접 링크
- US/US ETF: 스캔 시 Toss WTS autocomplete에서 product code를 best-effort로 캐시
- product code 미확보 시 Toss 홈을 열고 해당 티커를 클립보드에 복사

토스증권 페이지는 제3자 사이트이므로 PWA 내부 iframe으로 강제 임베드하지 않습니다.
