# 세이브티커 확장 웹 설치 + 계정 정보 설정 화면 입력 (설계)

날짜: 2026-11-08 · 크기: 작은 변경(짧은 설계)

## 의도
사용자는 세이브티커 확장 첫 설치(`trader saveticker extension install`)와 로그인 정보 저장(`trader saveticker login set`)을 터미널 명령으로 하는 것이 불편하다. 설치형 앱은 `trader` 가 PATH 에 없어 `/Applications/Trader.app/Contents/MacOS/trader …` 전체 경로를 쳐야 하고, 첫 실행 때 확장 설치를 묻지도 않는다. 웹 UI 에서 클릭으로 설치하고, 계정 정보는 「설정」(연결 키 설정)에서 넣게 한다.

## 목표 / 비목표
- 목표: 확장 미등록이면 웹 UI 상단 안내로 설치를 권하고 클릭 한 번으로 복사·Native host 등록. 시스템 › 세이브티커 화면에서 언제든 설치/갱신·상태 확인. 연결 키 설정에 세이브티커 이메일·비밀번호 묶음.
- 비목표: Chrome 「압축해제된 확장 로드」 자동화(Chrome 이 막음 — 경로 복사·안내만). 앱 업데이트 때의 자동 갱신(이미 데몬 `bridge_install.sync` 가 함). CLI 제거(그대로 둔다).
- 반목표: 원격(다른 PC·터널)에서 설치·비밀번호 저장이 되는 것. 비밀번호가 화면·응답·로그에 다시 보이는 것.

## 접근 (사용자 선택: A + 가)
- 계정 정보 — **A(선택)**: `features/accounts/secrets_io.CATALOG` 에 `("SAVETICKER_EMAIL", "세이브티커", "세이브티커 로그인 이메일")`, `("SAVETICKER_PASSWORD", "세이브티커", "세이브티커 로그인 비밀번호")` 추가 → 기존 연결 키 화면·저장·지우기·백업·값 숨김 재사용. 제약: 공백·`${`·따옴표/`#` 시작 값은 기존 검증이 거절(그런 비밀번호는 CLI `login set`). B(전용 칸)는 저장 방식이 둘이 되고 작업이 커서 제외.
- 설치 — **가(선택)**: 웹 라우트가 `bridge_install.install()` 을 바로 호출해 결과를 즉시 보여 준다. 보호는 연결 키와 같은 `require_token + require_same_origin + require_direct_local`. 나(데몬 잡 위임)는 결과를 바로 못 보여 줘서 제외. "웹은 원장+큐만" 불변식의 예외로 연결 키 설정(.env 직접 쓰기)과 나란히 둔다 — CLAUDE.md 불변식 줄에 예외로 명시.

## 설계
- API (`features/saveticker/routes.py`):
  - `GET /system/saveticker` 응답에 `install`(= `install_status()`: registered·package·installed/bundled 버전·update_needed + `extension_dir`)과 `onboarding`(`"dismissed"` 여부) 추가. 경로 노출은 로컬 화면용 — 기존 GET 은 토큰 없음이므로 경로는 `require_direct_local` 통과 시에만 넣는다(아니면 null).
  - `POST /system/saveticker/install` (LOCAL_WRITE): `install()` 호출 → `{ok, extension, next_steps, replaced}`; `InstallError` 는 400 + 한국어 메시지.
  - `POST /system/saveticker/onboarding` (WRITE): `{"dismissed": true}` → control 플래그 `saveticker.onboarding_dismissed` + `control_log`.
- `credentials.load()` 는 이미 `${}` 치환·공백 제거 없이 읽으므로 연결 키 화면이 쓴(따옴표 없는) 값도 그대로 읽힌다 — 테스트로 고정.
- 웹:
  - 셸 상단 배너(`SavetickerOnboarding`): `install.registered=false` 이고 dismissed 아님이면 표시. 「설치하기」→ 설치 API → 단계 카드(① 완료 표시 ② `chrome://extensions` 안내 + 확장 폴더 「경로 복사」 ③ 「설정에서 로그인 정보 입력」 링크 ④ 팝업에서 수집 시작). 「나중에」= 이번 세션만 닫기(sessionStorage), 「안 씀」= onboarding API.
  - 시스템 › 세이브티커(`SavetickerStatus.tsx`)에 「확장 설치」 카드: 등록·설치 버전·최신 여부, 「설치/갱신」, 「경로 복사」, 로그인 정보 저장 여부(연결 키 화면 링크).
- 문서: `docs/user-guide.md` 4-5, `docs/bundle-usage.md` 6절을 웹 기준으로, CLAUDE.md 웹 불변식 예외 한 줄.

## 빌더 몫
배너 위치·문구 세부, 카드 레이아웃(기존 시스템 화면 패턴), 테스트 구성(라우트 pytest, vitest 화면 테스트).
