# 바닥의 민족 v8.7 — 종목별 최신 기사 5개

종목 행/카드를 펼치면 차트 바로 아래에 최신 기사 헤드라인 5개를 불러옵니다.
기사 제목을 누르면 새 브라우저/탭으로 링크가 열립니다.

## 1. Google Apps Script 뉴스 프록시 만들기

1. 브라우저에서 `script.google.com` → **새 프로젝트**
2. 기본 `Code.gs` 내용을 모두 지우고 이 ZIP의 `news_proxy/Code.gs` 전체를 붙여넣습니다.
3. **배포 → 새 배포**
4. 유형: **웹 앱**
5. 실행 사용자: **나**
6. 액세스 권한: **모든 사용자(Anyone)**
7. 배포하고 권한 요청을 승인합니다.
8. 생성된 `/exec` URL을 복사합니다.

## 2. Morning Invest에 URL 넣기

`docs/news-config.js`를 열어:

```js
window.BADAK_NEWS_PROXY_URL = 'PASTE_YOUR_APPS_SCRIPT_EXEC_URL_HERE';
```

를 예를 들어:

```js
window.BADAK_NEWS_PROXY_URL = 'https://script.google.com/macros/s/xxxxxxxxxxxxxxxx/exec';
```

로 바꿉니다. 이 URL은 API 비밀키가 아니라 공개 웹앱 주소입니다.

## 3. GitHub에 교체/추가

- `docs/app.js`
- `docs/styles.css`
- `docs/index.html`
- `docs/news-config.js` (신규)
- `.github/workflows/update-and-deploy.yml`
- `firebase.json`
- `news_proxy/Code.gs` (보관용)
- `news_proxy/appsscript.json` (보관용)

기사 기능은 점수/시장 데이터 재계산과 무관하므로 **Actions → UI_ONLY**만 실행하면 됩니다.

## 동작

- 종목 클릭 즉시 최신 기사 로딩 시작
- KR: 종목명 + 코드 + `주식`으로 검색
- US: 종목명 + 티커 + `stock`으로 검색
- US ETF: 종목명 + 티커 + `ETF`로 검색
- 최신순 5개
- 제목 / 언론사 / 게시 시각 표시
- 제목 클릭 → 기사 링크
- 같은 종목은 브라우저 세션에서 5분간 캐시
- 프록시가 실패해도 `Google News에서 보기` 링크 제공
- Android `바닥의 민족` WebView 앱도 Firebase 웹을 읽으므로 APK 재빌드 불필요
