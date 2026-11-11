# KNOWLEDGE BATTLE v1.0

**2~4인 실시간 퀴즈 전술전.** 정사각형 해상 전장, 피해/연기/화재 애니메이션, 퀴즈 4개 카테고리, 실시간 관전, Firebase 권한 분리.

## 빠른 체험 (Firebase 필요 없음)

```bash
cd knowledge-battle
python -m http.server 5173 --directory docs
```

브라우저에서 `http://localhost:5173`을 열어 **AI와 전투 시작**을 누릅니다. ZIP과 별도로 제공하는 `Knowledge_Battle_Demo.html`은 압축 해제 및 서버 설치 없이 단독으로 열 수 있는 실행형 체험판입니다. 2/3/4명 AI 대전이 작동합니다. *AI 체험 문제는 각 카테고리의 별도 샘플이며, 서버의 전체 4,804문제는 온라인 모드에서 사용합니다.* 로컬 파일(`file://`) 대신 웹서버로 열어야 합니다.

## 기존 GitHub `DEMASIA90/stock_search` 전체 교체

1. 저장소를 클론하거나 GitHub Desktop에서 엽니다. 현재 파일을 **백업**한 후 `.git` 폴더만 보존하고 이전 코드·문서·Actions·`docs/data` 등은 제거합니다. (이 ZIP에는 과거 주식 코드가 포함되어 있지 않습니다.)
2. 이 ZIP 안의 **`knowledge-battle` 폴더 내 모든 파일**을 저장소 루트에 복사합니다. `.github` 폴더도 포함합니다.
3. 기존 Firebase 프로젝트가 `morninginv`라면 `.firebaserc`/workflow를 그대로 이용합니다. `dtc-lab` 등 다른 프로젝트라면 `.firebaserc`, workflow `FIREBASE_PROJECT_ID`, 서비스계정 Secret 이름을 해당 프로젝트에 맞게 변경합니다.
4. `docs/firebase-config.js`에 Firebase Console → 프로젝트 설정 → 웹 앱의 `apiKey`, `authDomain`, `projectId`, `appId`를 입력합니다. **이 값은 공개 클라이언트 설정**이며 서비스 계정 키와 다릅니다. 서비스 계정 JSON 파일은 절대 `docs/`에 넣지 마세요.
5. Firebase Console에서 **Authentication → 익명(Anonymous)** 사용 설정, **Cloud Firestore** 데이터베이스 생성을 진행합니다. Cloud Functions 2세대 배포에는 **Blaze 종량제 결제 플랜**이 필요합니다. 리전은 `asia-northeast3` (서울)입니다.
6. 아래의 문제 시드 / Firebase 배포를 실행합니다. Firestore 질문의 정답은 보안 규칙으로 클라이언트 직접 조회가 불가능합니다.

```bash
npm install
npm install --prefix functions
npm test
# ADC: gcloud auth application-default login  또는  GOOGLE_APPLICATION_CREDENTIALS 경로 지정
# 서비스계정에 Firestore 쓰기 권한 필요; 유료 API/쿼터는 본인 프로젝트에서 확인
FIREBASE_PROJECT_ID=morninginv npm run seed
npx firebase login
npx firebase deploy --only hosting,firestore:rules,functions --project morninginv
```

Windows PowerShell에서는 시드 실행 전 `$env:FIREBASE_PROJECT_ID="morninginv"`로 환경 변수를 지정할 수 있습니다. 인증은 `gcloud auth application-default login`으로 실행하거나 `GOOGLE_APPLICATION_CREDENTIALS`로 서비스계정 JSON 경로를 지정합니다.

### GitHub 자동 배포

`.github/workflows/firebase-deploy.yml`은 main 브랜치 push 시 자동 배포하도록 설정되어 있습니다. Repository Settings → Secrets and variables → Actions에 **`FIREBASE_SERVICE_ACCOUNT_MORNINGINV`** (대상 Firebase 프로젝트에 접근 가능한 서비스 계정 JSON 전문)를 등록하세요. 기존 Secrets를 그대로 재사용하는 경우에도 권한을 확인해야 합니다. DB 시드 등록은 **처음 1회 로컬에서** 실행하며 자동 배포에서 정답 DB를 매번 덮어쓰지 않습니다.

## 게임 규칙

| 명령 | 퀴즈 | 맞혔을 때 |
|---|---|---|
| 트라이던트 II 핵공격 | 과학 | 생존자 **모두(자신 포함)** HP -5 |
| 미니트맨 III ICBM | 영어 | **자신 제외** HP가 가장 높은 생존자 1명 HP -10; 동률이면 낮은 좌석 번호 우선 |
| 아이언돔 방어체계 | 역사 | **다음 피격 1회**에 한해 핵 피해 50%, ICBM 피해 100% 감면 |
| 전투공병 긴급복구 | 일반상식 | **마지막에 실제로 받은** 핵 피해의 50% / ICBM 피해의 100% 회복(피해 기록 소비) |

* 체력은 `0~100`, 핵 방어로 2.5 소수 체력 가능. 공격은 HP 0 플레이어에 적용되지 않습니다. 보호막의 전액 차단으로 피해가 0이라면 복구 기록은 생기지 않습니다.
* 틀리면 행동 미실행. 시간 제한 30초, 만료 시 오답 처리. 정답/오답 및 선택지는 방에 참여한 모든 플레이어에게 실시간 표시됩니다.
* 살아남은 플레이어가 1명이면 승리; 전원 동시 전멸은 무승부. 최소 2명이 참여하면 방장이 시작할 수 있으며 정원(2~4명)은 방 생성 시 결정합니다.
* 무기와 기술 명칭은 실존 사례에서 영감을 받았으나, 피해량 및 방어 성능은 게임 밸런싱을 위한 허구입니다.

## 질문 DB

`data/questions.json`에 **총 4,804개의 4지선다 문제/정답**이 저장되어 있습니다. 과학 1,200개 / 영어 1,200개 / 역사 1,200개 / 일반상식 1,204개. `tools/seed_firestore.mjs`가 서버 전용 Firestore `questions/{id}`에 BulkWriter로 적재합니다. `functions/selectAction`은 카테고리별 랜덤 ID를 선택하고 정답을 반환하지 않습니다. `submitAnswer`가 서버에서 정답 판정합니다.

### 콘텐츠 품질 주의

이는 **1차 생성형 문제은행**입니다. 원소·공식·영단어·역사 연도·국가 정보 기반의 정확한 데이터를 활용하나, 동일 원자료를 다른 방식으로 묻는 문제가 포함됩니다. 외부 서비스 출시 전 한국어 문장, 역사 연도/표기, 영어 중의성, 국가 최신 정보를 **교정 및 검수**해야 합니다. 문제 수는 검증되어 있지만 4,804개의 서로 다른 지식 사실을 뜻하지는 않습니다.

신규 문제 추가는 기존 스키마 `{id,category,prompt,options,correctIndex,explanation}`로 저장하고 `functions/index.js`의 `CATEGORIES` 최대 ID도 늘리면 됩니다. 연속 인덱스가 없는 외부 데이터셋을 사용하려면 DB의 랜덤 문제 선택 로직을 추가로 조정합니다. 기존 문제 수정은 Firestore 시드 재실행으로 반영합니다.

## 파일 역할

- `docs/index.html`, `styles.css`, `app.js`: 화면 / 로컬 AI / 실시간 관전 / 파괴 이펙트
- `docs/firebase-config.js`: 본인의 Firebase 웹 설정 (초기에는 비어 있음)
- `docs/manifest.webmanifest`, `assets/icon.svg`: PWA 스타일의 앱 아이콘과 메타데이터
- `functions/index.js`: 온라인 명령, 서버 사이드 정답 처리, 방 및 턴 관리
- `functions/engine.js`: HP/방어/수리/공격 순수 함수
- `firestore.rules`: 회원만 방 읽기, 클라이언트 문제 정답 읽기/쓰기 금지
- `data/questions.json`: 정답 포함 서버용 시드, **Hosting docs 밖에 보관**
- `tools/generate_questions.py`, `seed_firestore.mjs`: 문제생성 및 Firebase 적재
- `tests/engine.test.js`: 주요 게임 규칙 자동 테스트

## 현 버전 한계 / 다음 개발 후보

현재는 Firebase 인증·실시간 동기화·문제 출제·턴제 전투의 **기초 베타**입니다. 인터넷 연결 중단 시 자동 일시정지/재접속 처리는 일부만 지원하며, 계정 통합, 친구 초대 알림, 반복 출제 방지, 문제 난이도, 국가 커스터마이징, 랭킹, 매치 이력, BGM, 서버 TTL 방 정리, 관리자 CMS 등은 아직 미구현입니다. 악성 클라이언트 호출 및 비용 남용 방지를 위해 공개 서비스 전 Firebase **App Check**, API/호출 쿼터 모니터링, 레이트 리미팅을 추가하는 편이 좋습니다.

### Windows GitHub Desktop 사용자를 위한 일괄 교체 (선택)

GitHub Desktop의 `Repository > Show in Explorer`로 로컬 저장소 경로를 확인한 후 PowerShell에서 실행합니다. 원본 ZIP은 저장소 **바깥**에 압축 해제하세요. 이 명령은 `.git`을 보존하면서 **기존 파일을 자동 백업하고 전부 교체**합니다.

```powershell
& '.\knowledge-battle\tools\replace_repo.ps1' -RepoPath 'C:\Users\사용자\Documents\GitHub\stock_search'
```

교체 후 GitHub Desktop의 `Changes` 목록을 검토하고 Commit/Push하세요. 자동 배포 전에 Firebase 인증·프로젝트 설정 및 결제 플랜이 준비되어 있는지 확인하세요.

**주의:** GitHub 파일/Hosting 교체는 기존 Firebase의 Firestore 주식 관련 컬렉션을 자동 삭제하지 않습니다. 해당 데이터를 영구 삭제하려면 대상 프로젝트와 남겨야 할 컬렉션을 별도 확인한 뒤 Firebase 콘솔에서 정리해야 합니다.
