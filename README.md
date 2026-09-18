# Market Watch & Yield Monitor

기존 `stock_search` 저장소의 GitHub·Firebase Hosting 연결을 유지하면서, 주문 기능 없이 시장 신호와 개인 계좌 수익을 조회하는 웹입니다.

## 관심종목

다음 유니버스를 매시간 분석합니다.

- 미국 증시 3배 레버리지·인버스 ETF
- 원화 환산 시가총액 500조원 이상 미국 상장기업
- 시가총액 100조원 이상 한국 상장기업

볼린저밴드(20일·2표준편차)의 상단과 하단 사이를 동일 폭으로 5등분해 `최하단·하단·중단·상단·최상단`으로 표시합니다. 밴드가 낮은 종목을 먼저 배치하고, 같은 구간에서는 다음 점수의 합계가 높은 종목부터 표시합니다.

- Supertrend(10,3) 상승: 10점
- Supertrend(20,4) 상승: 10점
- 60일 이동평균의 최근 5거래일 기울기 양수: 5점
- 200일 이동평균의 최근 5거래일 기울기 양수: 5점

섹터, 원화 환산 시가총액, 현재가, 전일 대비 등락률, 60일 평균가격 대비 등락률을 함께 제공합니다.

## Yield Monitor

숫자 4자리 PIN을 입력해야 열립니다. NH투자증권 Namuh PLUG의 조회 전용 API로 다음 정보를 갱신합니다.

- 통합 총자산, 현재 평가손익과 최근 30일 매수·매도
- 국내 종목별 실현손익과 미국 기간손익 상세
- 입금·출금 이벤트를 수익과 분리한 누적 기록
- 현재 보유종목의 볼린저 5단계, Supertrend(14,3), 60·200일선 기울기
- 섹터별 평가수익률, 월별 실현수익률, 평균 익절·손절률

매번 최근 30일을 다시 조회해 중복을 제거하고, 과거 실현손익·입출금·시간별 자산 스냅샷은 암호화 데이터 안에 계속 누적합니다. 그래프의 점을 누르면 해당 날짜의 실현 종목과 입출금을 확인할 수 있습니다.

## 처음 배포

1. Python 3.11~3.14, Git for Windows를 설치합니다.
2. `first_deploy.bat`을 실행합니다.
3. NHPLUG AppKey/AppSecret, 환경, 계좌, 숫자 4자리 PIN을 입력합니다.
4. GitHub 푸시 확인에 `Y`를 입력합니다.
5. 완전 자동 갱신을 사용하려면 [GitHub CLI](https://cli.github.com/)를 설치하고 로그인한 다음 `configure_hourly_update.bat`을 한 번 실행합니다.

`configure_hourly_update.bat`은 Windows 자격 증명 저장소의 값을 GitHub Actions Secret으로 안전하게 등록합니다. 이후 GitHub Actions가 매시 17분에 관심종목과 계좌를 조회하고, 암호화 이력을 커밋한 뒤 Firebase와 GitHub Pages에 배포합니다.

## 수동 업데이트

- 관심종목과 계좌 즉시 업데이트: `publish_update.bat`
- 계좌 재선택: `change_account.bat`
- PIN 변경: `change_web_password.bat`
- 시간당 자동 갱신 설정: `configure_hourly_update.bat`
- 로컬 미리보기: `preview_local.bat`
- 코드 검사: `test.bat`

## 보안과 범위

API 키, AppSecret, 계좌번호, PIN은 정적 웹 파일에 포함되지 않습니다. GitHub에는 공개 관심종목 JSON과 PIN으로 암호화된 개인 데이터만 커밋됩니다. 시간당 서버 업데이트를 켜면 조회 자격증명과 계좌번호, PIN은 GitHub Actions Secret에도 저장됩니다.

숫자 4자리 PIN은 조합이 10,000개뿐이므로 강한 보안수단이 아닙니다. Firebase Authentication 수준의 기밀성이 필요하면 현재 구조 대신 인증 서버를 추가해야 합니다. 자세한 내용은 [SECURITY.md](SECURITY.md)를 확인하세요.

웹에는 매수·매도 주문, 자동매매, HTS 데스크톱 UI가 없습니다.
