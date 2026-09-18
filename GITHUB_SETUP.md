# 기존 stock_search 저장소 배포 및 시간당 업데이트

이 압축본에는 기존 `stock_search` 저장소의 Git 연결 정보가 보존되어 있습니다. 새 저장소를 만들지 않고 기존 `main` 브랜치와 Firebase Hosting을 그대로 사용합니다.

## 1. 교체 배포

1. 압축을 새 폴더에 풉니다.
2. `first_deploy.bat`을 실행합니다.
3. 계좌 조회와 암호화가 끝나면 표시되는 변경 목록을 확인합니다.
4. 질문에 `Y`를 입력하면 기존 저장소의 `main` 브랜치로 푸시합니다.
5. GitHub **Actions**에서 `Market Watch & Yield Monitor · Update and Deploy`가 성공하는지 확인합니다.

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

가장 간단한 방법은 [GitHub CLI](https://cli.github.com/)를 설치해 `gh auth login`을 완료한 후 `configure_hourly_update.bat`을 실행하는 것입니다. 최초 배포 때 Windows 자격 증명 저장소에 저장한 값을 다음 GitHub Actions Secret으로 등록합니다.

- `NHPLUG_APP_KEY`
- `NHPLUG_APP_SECRET`
- `ASSET_WEB_ACCOUNT`
- `PORTFOLIO_PIN`

그리고 조회 환경을 Variable `ASSET_WEB_ENV`에 `live` 또는 `mock`으로 등록합니다.

GitHub 웹에서 직접 입력해도 됩니다. 계좌번호는 하이픈 없이 NHPLUG 계좌 목록에서 반환되는 11자리 값을 사용합니다. 필요하면 Variable `USD_KRW_RATE`에 시가총액 필터용 원·달러 환율을 넣을 수 있으며, 미설정 시 1,400원을 사용합니다.

설정 후 워크플로는 매시 17분에 다음 작업을 수행합니다.

1. 관심종목 시세와 기술지표 계산
2. 계좌의 총자산·최근 30일 거래·실현손익·입출금 조회
3. 기존 암호화 이력과 병합
4. `docs/data/watchlist.json`과 `docs/data/portfolio.enc.json` 커밋
5. Firebase Hosting과 GitHub Pages 배포

GitHub Actions의 예약 실행은 정확히 17분에 시작되지 않고 서버 상황에 따라 지연될 수 있습니다. 저장소의 Actions 권한이 읽기 전용이면 **Settings → Actions → General → Workflow permissions**에서 `Read and write permissions`를 허용해야 시간별 이력 커밋이 가능합니다.

## PIN 변경

1. 먼저 `change_web_password.bat`을 실행해 현재 암호문을 새 PIN으로 바꾸고 푸시합니다.
2. 이어서 `configure_hourly_update.bat`을 다시 실행해 GitHub의 `PORTFOLIO_PIN` Secret을 새 값으로 갱신합니다.

두 작업 사이에 예약 실행이 시작되면 한 번 실패할 수 있으나, Secret을 갱신한 뒤 **Actions → Run workflow**에서 다시 실행하면 됩니다.
