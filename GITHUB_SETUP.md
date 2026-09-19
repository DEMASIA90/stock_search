# 기존 stock_search 저장소 배포 및 시간당 업데이트

수정본 ZIP에는 `.git` 폴더를 포함하지 않습니다. **기존 `stock_search` Git 저장소 폴더에 파일을 덮어써서 사용**해야 누적 이력과 원격 연결이 유지됩니다. `docs/data/portfolio.enc.json`도 수정본 ZIP에서 제외하므로 기존 누적 데이터는 그대로 보존됩니다.

## 1. 교체 배포

1. 기존 `stock_search` 저장소를 먼저 최신 상태로 `git pull` 합니다.
2. 수정본 ZIP의 파일을 **기존 저장소 폴더 위에 덮어씁니다.** 새 독립 폴더에서 시작하지 마세요.
3. `first_deploy.bat` 또는 `publish_update.bat`을 실행합니다.
4. 계좌 조회와 암호화가 끝나면 표시되는 변경 목록을 확인합니다.
5. 질문에 `Y`를 입력하면 기존 저장소의 `main` 브랜치로 푸시합니다.
6. GitHub **Actions**에서 `Market Watch & Yield Monitor · Update and Deploy`가 성공하는지 확인합니다.

직접 실행하려면 다음 순서로 진행합니다.

```powershell
setup.bat
.venv\Scripts\python.exe update_watchlist.py
.venv\Scripts\python.exe update_portfolio.py
git add -A
git commit -m "Add market watch and yield monitor"
git push origin main
```

## 2. Firebase 배포용 기존 설정

GitHub 저장소의 **Settings → Secrets and variables → Actions**에 기존 값이 있어야 합니다.

Variables:

- `FIREBASE_PROJECT_ID`
- `FIREBASE_SITE_ID`

Secrets:

- `FIREBASE_SERVICE_ACCOUNT_JSON`

## 3. 매시간 자동 계좌 업데이트 설정

`configure_hourly_update.bat`을 실행하면 됩니다. GitHub CLI(`gh`)가 PC에 없으면 관리자 권한 없이 프로젝트의 `.tools/github-cli` 폴더에 **portable GitHub CLI를 자동 설치**합니다. GitHub 인증이 아직 없으면 브라우저 로그인도 이어서 실행합니다. 자동 설치가 사내망/보안 프로그램 때문에 실패할 때만 [GitHub CLI](https://cli.github.com/)를 수동 설치하면 됩니다. 최초 배포 때 Windows 자격 증명 저장소에 저장한 값을 다음 GitHub Actions Secret으로 등록합니다.

- `NHPLUG_APP_KEY`
- `NHPLUG_APP_SECRET`
- `ASSET_WEB_ACCOUNT`
- `PORTFOLIO_PIN`

그리고 조회 환경을 Variable `ASSET_WEB_ENV`에 `live` 또는 `mock`으로 등록합니다.

GitHub 웹에서 직접 입력해도 됩니다. 계좌번호는 하이픈 없이 NHPLUG 계좌 목록에서 반환되는 11자리 값을 사용합니다. 필요하면 Variable `USD_KRW_RATE`에 시가총액 필터용 원·달러 환율을 넣을 수 있으며, 미설정 시 1,400원을 사용합니다.

설정 후 워크플로는 매시 17분에 다음 작업을 수행합니다.

1. 계좌의 총자산·최근 30일 거래·실현손익·입출금 조회를 **가장 먼저** 수행
2. 기존 암호화 이력과 병합하여 `docs/data/portfolio.enc.json` 갱신
3. 관심종목 시세와 기술지표 계산 (일시 실패해도 Yield Monitor 누적은 계속 진행)
4. 회귀테스트 후 생성 데이터를 커밋/Push
5. Firebase Hosting 배포 (일시 오류 시 최대 3회 재시도)

해외주식 거래는 `dailyTransaction`을 매수(05)·매도(06)로 각각 조회하고 연속조회 페이지를 모두 수집합니다. `periodPnl`은 매도일 확인용, 종목별 손익은 `periodPnlDetail`을 사용합니다. 미국 손익 조회는 `iqr_dit=2`(원화 기준 결과)와 실제 거래통화 `trd_cur_cd=USD`, 국가코드 `200`을 함께 사용합니다. 손익 상세가 없더라도 매도 종목/수량/가격은 거래내역으로 보존되고 실현손익은 `—`로 표시됩니다.

GitHub Actions의 예약 실행은 정확히 17분에 시작되지 않고 서버 상황에 따라 지연될 수 있습니다. 저장소의 Actions 권한이 읽기 전용이면 **Settings → Actions → General → Workflow permissions**에서 `Read and write permissions`를 허용해야 시간별 이력 커밋이 가능합니다.

## PIN 변경

1. 먼저 `change_web_password.bat`을 실행해 현재 암호문을 새 PIN으로 바꾸고 푸시합니다.
2. 이어서 `configure_hourly_update.bat`을 다시 실행해 GitHub의 `PORTFOLIO_PIN` Secret을 새 값으로 갱신합니다.

두 작업 사이에 예약 실행이 시작되면 한 번 실패할 수 있으나, Secret을 갱신한 뒤 **Actions → Run workflow**에서 다시 실행하면 됩니다.

## 자동 업데이트 실패 확인

`check_hourly_update.bat`을 실행하면 최근 6개 Actions 실행 상태와 가장 최근 실패 단계의 로그를 바로 표시합니다.

`configure_hourly_update.bat`은 Secret/Variable을 저장한 뒤 워크플로를 활성화하고 즉시 1회 검증 실행도 요청합니다. 따라서 다음 정시 실행까지 기다릴 필요가 없습니다.

자주 보는 실패 지점은 다음과 같습니다.

- `Validate hourly-update settings`: GitHub Secret/Variable 누락 또는 PIN 형식 오류
- `Update encrypted portfolio history`: 계좌/인증/PIN 또는 NHPLUG API 응답 문제
- `Commit cumulative data`: Actions의 `contents: write` 권한 또는 브랜치 보호 규칙 문제
- `Deploy docs ... Firebase`: 서비스계정 JSON, Project ID, Site ID 또는 Firebase 권한 문제. 배포는 최대 3회 재시도하며, 이 단계까지 왔다면 암호화 포트폴리오 데이터는 이미 GitHub에 커밋된 상태입니다.
