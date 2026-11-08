# 세이브티커 확장 웹 설치 + 계정 정보 설정 입력 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 웹 UI 클릭으로 세이브티커 Chrome 확장을 설치하고(첫 안내 배너 + 시스템 화면 카드), 로그인 이메일·비밀번호는 연결 키 설정에서 넣는다.

**Architecture:** `features/saveticker/routes.py` 에 설치·안내 닫기 API 를 더하고 GET 상태에 `install`·`onboarding` 을 싣는다. 설치는 웹이 `bridge_install.install()` 을 바로 부른다(연결 키 설정과 같은 LOCAL_WRITE 보호). 계정은 `features/accounts/secrets_io.CATALOG` 두 줄. 웹은 `Shell` 상단 배너와 `SavetickerStatus` 카드.

**Tech Stack:** FastAPI·pytest(TestClient), React 18·vitest.

**Spec:** `docs/superpowers/specs/2026-11-08-saveticker-web-install-design.md`

## Global Constraints
- 설치 POST·계정 저장은 `require_token + require_same_origin + require_direct_local` (accounts `LOCAL_WRITE` 와 동일). 안내 닫기 POST 는 `require_token + require_same_origin`.
- 확장 경로(`package`·`extension_dir`·`registration`·`host_path`)는 `is_direct_local(request)` 일 때만 응답에 싣고 아니면 `null`.
- 비밀번호 값은 어떤 응답·로그에도 없다.
- control 플래그 키: `saveticker.onboarding_dismissed` (값 `{"at","actor"}`), `control.log_change(channel="web")` 로 기록.
- 사용자 문구는 한국어. Windows: 텍스트 I/O 는 `encoding="utf-8"`, POSIX 권한 검사는 `sys.platform != "win32"` 에서만.
- CLI `extension install`·`login set` 은 그대로 둔다.

## Review Focus
- 원격(tailscale 등 프록시 헤더)에서 GET 은 경로 null, POST install 은 403 — Task 1 테스트.
- `InstallError`(예: `registration_exists`, `app_translocated`) → 400 + 한국어 `detail`, 500 이 아니다 — Task 1 테스트.
- 연결 키 화면이 쓴 따옴표 없는 비밀번호(`a#b\c`)를 `credentials.load()` 가 그대로 읽는다 — Task 2 테스트.
- 배너: 이미 등록됐거나 dismissed 면 안 보이고, 「나중에」 뒤 같은 세션 새로고침에도 안 보인다 — Task 3 테스트.
- 설치 실패 시 배너가 오류 문구를 보이고 버튼이 다시 눌린다 — Task 3 테스트.

---

### Task 1: 세이브티커 설치·안내 API

**Files:**
- Modify: `src/trader/features/saveticker/routes.py`
- Modify: `CLAUDE.md` (불변식 「웹은 원장 + 큐만」 줄의 예외에 "로컬 직접 접속 한정 쓰기: 연결 키 설정(`secrets/.env`)·세이브티커 확장 설치(`bridge_install.install`)" 추가)
- Test: `tests/features/saveticker/test_routes.py`

**Interfaces:**
- Produces: `GET /api/system/saveticker` 에 `install: {registered: bool, installed_version: str|null, bundled_version: str|null, update_needed: bool, package: str|null, extension_dir: str|null}` (`extension_dir` = `package + "/extension"`, 원격이면 package·extension_dir null) 과 `onboarding: {dismissed: bool}` 추가. `POST /api/system/saveticker/install` → `{ok: true, extension: str, next_steps: list[str], replaced: bool}`, 실패 400 `{detail: "<한국어>"}`. `POST /api/system/saveticker/onboarding` body `{"dismissed": true}` → `{ok: true}`. 상수 `ONBOARDING_FLAG = "saveticker.onboarding_dismissed"`.

- [ ] **Step 1: 실패 테스트** — `test_routes.py` 에 로컬 클라이언트(`TestClient(client.app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1")`, 헤더 `{"Authorization": "Bearer t", "Origin": "http://127.0.0.1"}` — accounts 테스트의 LOCAL/LOCAL_AUTH 패턴)와 `monkeypatch` 로 `bridge_install.install`·`install_status` 를 가짜로 바꿔:
  - `test_status_includes_install_and_onboarding`: 로컬 GET → `install.registered is False`, `install.extension_dir.endswith("/extension")`, `onboarding == {"dismissed": False}`; 원격(기존 `client`) GET → `install.package is None and install.extension_dir is None`.
  - `test_install_requires_local_token_origin`: 토큰 없음 401, 다른 Origin 403, 프록시 헤더(`X-Forwarded-For`) 403, 로컬 → 200 `{"ok": True, "extension": ..., "next_steps": [...], "replaced": False}` 이고 가짜 install 이 1회 호출.
  - `test_install_error_is_korean_400`: 가짜가 `InstallError("registration_exists")` → 400, `detail` 에 "다른 프로그램" 포함, 영문 코드만 노출 안 됨.
  - `test_onboarding_dismiss_persists_and_logs`: POST `{"dismissed": True}` → 200; 이후 GET `onboarding.dismissed is True`; `control_log` 에 key `saveticker.onboarding_dismissed` 1행.
- [ ] **Step 2:** `uv run pytest -q tests/features/saveticker/test_routes.py` → 새 4건 FAIL(404·KeyError).
- [ ] **Step 3: 구현** — `routes.py`: `LOCAL_WRITE = [*WRITE, Depends(require_direct_local)]`; `INSTALL_ERRORS: dict[str, str]` 로 코드 9개(`app_translocated` "앱을 응용 프로그램 폴더로 옮긴 뒤 실행하세요", `registration_exists`·`registration_owner_mismatch` "다른 프로그램이 같은 이름으로 등록돼 있습니다", `stop_extension_first` "확장 팝업에서 수집을 중지한 뒤 다시 하세요", 나머지는 "설치 파일이 손상됐습니다(…)" 류)를 한국어로, 모르는 코드는 "확장 설치에 실패했습니다". 상태 GET 은 `install_status()` 를 try 로 감싸 실패 시 `registered False` 기본값. 함수 안에서 `from trader.features.saveticker import bridge_install` (테스트 monkeypatch 가 모듈 속성을 바꾸므로 `bridge_install.install()` 로 호출).
- [ ] **Step 4:** 같은 명령 → 전부 PASS. `uv run ruff check src tests`.
- [ ] **Step 5: 커밋** `feat(saveticker): 웹에서 확장 설치·첫 안내 닫기 API`

### Task 2: 연결 키 설정에 세이브티커 계정

**Files:**
- Modify: `src/trader/features/accounts/secrets_io.py` (`CATALOG` 끝에 두 줄)
- Test: `tests/features/saveticker/test_credentials.py`

**Interfaces:**
- Consumes: `secrets_io.update(path, accounts, values=..., clear=...)`(기존), `credentials.load()`(기존).
- Produces: catalog 항목 `("SAVETICKER_EMAIL", "세이브티커", "세이브티커 로그인 이메일")`, `("SAVETICKER_PASSWORD", "세이브티커", "세이브티커 로그인 비밀번호(계속 복구용)")`.

- [ ] **Step 1: 실패 테스트** `test_settings_screen_values_are_read_by_credentials(project)`: `secrets_io.update(paths.secrets_path(), [], values={"SAVETICKER_EMAIL": "u@x.com", "SAVETICKER_PASSWORD": "a#b\\c"})` 뒤 `credentials.load() == ("u@x.com", "a#b\\c")`; `secrets_io.catalog([])` 에 두 이름이 group "세이브티커" 로 있음; `update(..., clear=["SAVETICKER_EMAIL", "SAVETICKER_PASSWORD"])` 뒤 `credentials.load() is None`.
- [ ] **Step 2:** `uv run pytest -q tests/features/saveticker/test_credentials.py` → FAIL(`입력할 수 없는 키`).
- [ ] **Step 3:** CATALOG 두 줄 추가.
- [ ] **Step 4:** 위 명령 + `uv run pytest -q tests/features/accounts` → PASS(기존 catalog 개수 검사가 있으면 기대값 갱신).
- [ ] **Step 5: 커밋** `feat(accounts): 연결 키 설정에 세이브티커 로그인 정보`

### Task 3: 웹 — 설치 카드와 첫 안내 배너

**Files:**
- Modify: `web/src/screens/system/system.ts` (`SavetickerResp` 에 `install?`, `onboarding?` — Task 1 모양 그대로, 선택 필드)
- Create: `web/src/screens/system/SavetickerInstall.tsx` (카드 + 공용 설치 훅)
- Modify: `web/src/screens/system/SavetickerStatus.tsx` (카드 삽입)
- Create: `web/src/app/shell/SavetickerOnboarding.tsx` (배너), Modify: `web/src/app/shell/Shell.tsx` (StatusBar 아래 auth-notice 다음에 `<SavetickerOnboarding />`)
- Test: `web/src/screens/__tests__/saveticker-install.test.tsx`

**Interfaces:**
- Consumes: Task 1 API, `postJson`·`getJson`·`errorText`(`lib/api`), 기존 `Confirm`·`Chip`.
- Produces: `useSavetickerInstall(): { busy: boolean; result: InstallResult | null; error: string | null; install(): Promise<void> }`, `type InstallResult = { extension: string; next_steps: string[]; replaced: boolean }`, `<InstallSteps result />`(① 완료 ② `chrome://extensions` 안내 + 「경로 복사」(`navigator.clipboard.writeText(extension)`) ③ `<Link to="/system/admin#secrets">설정 › 연결 키 › 세이브티커</Link>` ④ 팝업 「수집 시작」).

- [ ] **Step 1: 실패 테스트** (fetch mock 은 기존 `system.test.tsx` 방식):
  - 배너: GET 이 `install.registered=false, onboarding.dismissed=false` → 「세이브티커 뉴스 수집을 쓰시겠어요?」 표시; `registered=true` 또는 `dismissed=true` → 미표시.
  - 「설치하기」 → POST `/system/saveticker/install` 1회 → 단계 카드에 응답 `extension` 경로와 「경로 복사」, 「chrome://extensions」 문구.
  - 설치 400 → 오류 문구 표시, 버튼 다시 활성.
  - 「나중에」 → 배너 사라지고 `sessionStorage["saveticker-onboarding-later"] === "1"`; 재렌더에도 미표시. 「안 씀」 → POST `/system/saveticker/onboarding` `{dismissed: true}`.
  - 시스템 카드: `installed_version "1.5.0"`, `update_needed false` → 「확장 1.5.0 · 최신」, 「설치/갱신」 버튼; `extension_dir null`(원격) → 「이 PC 에서 직접 연 화면에서만 설치할 수 있습니다」.
- [ ] **Step 2:** `cd web && npx vitest run src/screens/__tests__/saveticker-install.test.tsx` → FAIL(모듈 없음).
- [ ] **Step 3: 구현** — 배너는 `/system/saveticker` 를 1회 GET(실패·토큰 없음이면 조용히 숨김), sessionStorage 접근은 try/catch. 스타일은 기존 `auth-notice` 계열 클래스 재사용 + 최소 CSS.
- [ ] **Step 4:** `cd web && npm run typecheck && npx vitest run` → PASS.
- [ ] **Step 5: 커밋** `feat(web): 세이브티커 확장 설치 카드와 첫 안내 배너`

### Task 4: 문서

**Files:** `docs/user-guide.md`(4-5: 웹 「설치하기」→ Chrome 로드 → 설정 › 연결 키 › 세이브티커 → 팝업; 명령은 대안으로 한 줄), `docs/bundle-usage.md`(6절 1·3단계 웹 기준), `docs/saveticker-collection.md`(설치 블록에 웹 경로 한 줄), `src/trader/features/saveticker/README.md`(routes 설명).

- [ ] **Step 1:** 위 문서 갱신. **Step 2:** `uv run pytest -q tests/features/saveticker tests/features/accounts` 와 `cd web && npm run build` PASS. **Step 3: 커밋** `docs(saveticker): 웹 설치·설정 입력 안내`
