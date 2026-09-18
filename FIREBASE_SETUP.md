# Firebase Hosting 유지 사항

기존 Firebase 프로젝트와 Hosting 사이트를 그대로 사용하도록 구성했습니다. 저장소에 이미 설정된 GitHub Actions 변수와 Secret이 유효하면 추가 설정은 필요하지 않습니다.

## 유지되는 저장소 설정

GitHub 저장소의 **Settings → Secrets and variables → Actions**에 다음 값이 있어야 합니다.

- `FIREBASE_PROJECT_ID`: 기존 Firebase 프로젝트 ID
- `FIREBASE_SITE_ID`: 기존 Firebase Hosting 사이트 ID
- `FIREBASE_SERVICE_ACCOUNT_JSON`: Firebase 배포용 서비스 계정 JSON Secret

워크플로는 배포 시점에만 서비스 계정 JSON을 임시 파일로 만들고 작업 종료 전에 삭제합니다. 서비스 계정 JSON을 저장소 파일로 추가하지 마세요.

## 배포 트리거

다음 파일이 `main` 브랜치에 푸시되면 자동 배포됩니다.

- `docs/**`
- `firebase.json`
- `.firebaserc`
- `.github/workflows/update-and-deploy.yml`

자산 업데이트 배치가 데이터 파일을 푸시하면 위 조건에 따라 동일한 Firebase Hosting 사이트가 갱신됩니다. 시간당 자동 갱신을 설정한 경우 동일 워크플로가 매시 17분에 데이터를 조회하고, 암호화 이력을 커밋한 뒤 같은 Firebase Hosting 사이트에 바로 배포합니다.
