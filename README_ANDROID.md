# DTC v11.5 Android

DTC v11.5 scanner/web app에 Android 빌드 구성을 추가한 버전입니다.

## Android 동작 방식

Android 앱은 Capacitor가 `docs/`의 웹 UI를 APK에 포함하고, 주식 데이터 JSON만 Firebase Hosting에서 읽습니다.

- UI: APK 내부 `docs/`
- 실시간/스캔 데이터: `https://morninginv.web.app/data/...`

따라서 스캐너가 Firebase 데이터를 갱신하면 APK를 다시 설치하지 않아도 최신 종목/점수/차트 데이터를 읽습니다.
UI 코드(`docs/index.html`, `app.js`, `styles.css`) 자체를 변경한 경우에는 새 APK를 빌드해야 합니다.
`firebase.json`에는 Android WebView에서 JSON을 읽을 수 있도록 `/data/**` CORS 헤더가 포함되어 있습니다.

## GitHub Actions에서 APK 만들기

1. 이 프로젝트 전체를 GitHub 저장소 루트에 업로드합니다.
2. GitHub의 **Actions** 탭으로 이동합니다.
3. **DTC Android · Build APK & AAB** workflow를 선택합니다.
4. **Run workflow**를 실행합니다.
5. 완료 후 workflow 하단 Artifacts에서 `DTC-v11.5-Android-APK`를 받습니다.
6. 압축을 풀면 `app-debug.apk`가 있으며 Android 기기에 직접 설치할 수 있습니다.

`main` 또는 `master` 브랜치의 `docs/**`, `package.json`, `capacitor.config.json` 또는 Android workflow가 변경되어 push될 때도 자동 빌드됩니다.

## Play Store용 서명 APK/AAB

서명 Secret이 없더라도 install 가능한 debug APK와 unsigned release AAB는 생성됩니다.
Play Store에 올릴 서명된 release APK/AAB가 필요하면 GitHub Repository Settings > Secrets and variables > Actions에 아래 4개 Secret을 등록합니다.

- `ANDROID_KEYSTORE_BASE64`: JKS/keystore 파일을 Base64로 인코딩한 문자열
- `ANDROID_KEYSTORE_PASSWORD`: keystore 비밀번호
- `ANDROID_KEY_ALIAS`: key alias
- `ANDROID_KEY_PASSWORD`: key 비밀번호

4개가 모두 있으면 workflow가 release signing을 자동 적용하고 signed release APK/AAB를 생성합니다.

## 로컬 Android 프로젝트 생성

Node.js 22 이상과 Android Studio/JDK가 있는 PC에서:

```bash
npm install
npx cap add android
npx cap sync android
npx cap open android
```

이미 `android/`가 생성돼 있다면 `npx cap add android` 대신 `npx cap sync android`만 실행합니다.

## 주요 설정

- App ID: `com.dongtan.tradingcenter`
- App Name: `DTC`
- Bundled UI: `docs/`
- Market data source: `https://morninginv.web.app/data/...`
- Capacitor: `8.x`
- Android wrapper version: `11.3.x`
- Launcher icon: bundled DTC icon (`android-assets/`)
- Scanner algorithm component: `11.2`
