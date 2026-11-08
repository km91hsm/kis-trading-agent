# 단타 계좌별 켜기·끄기 + 일시정지·운영 점검 표기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 단타를 계좌별(모의·실전)로 웹에서 켜고 끄게 하고, 헬스·대시보드·계좌 화면·▲ 메뉴에서 「전체 일시정지」를 운영 점검·계좌 모드와 구분해 보이게 한다.

**Architecture:** 계좌별 스위치는 control 플래그 `scalp_enabled:<계좌id>` 이고, scalp 기능 안의 새 모듈 `switch.py` 가 저장값·실효 판정을 맡는다(모드·일시정지는 `ports["accounts"]` 로만 읽음). 잡·엔진은 전역 `scalp.enabled` 대신 이 판정을 쓴다. 일시정지 시각·경로는 커널의 새 조회 `GET /system/pause`(control `paused` + control_log 최근 행)로 웹 세 화면이 함께 쓴다. 트레이는 일시정지를 켤 때 OS 확인창을 띄운다.

**Tech Stack:** Python(FastAPI·pydantic·SQLite, pytest·ruff, uv), React + vitest, pystray.

**Spec:** `docs/superpowers/specs/2026-11-09-scalp-toggle-and-pause-labels-design.md`

## Global Constraints

- 기능 경계: scalp 는 accounts 를 직접 import 하지 않는다 — 계좌 목록·모드·일시정지는 `ctx.ports["accounts"]`(`accounts()`, `policy(conn, account) -> (mode, note)`, `paused(conn)`)로만. 커널은 기능 이름을 모른다.
- 실전 계좌 스위치 켜기는 본문 `confirm == 계좌 id` 아니면 400(`kernel/web/auth.require_confirm` 재사용). 끄기·기본값 복귀(null)는 확인 없음.
- 실전 계좌 기본값은 **꺼짐**(yaml `scalp.enabled` 와 무관). 모의 계좌 기본값 = yaml `scalp.enabled`.
- 실전 계좌 실효 = 스위치 켜짐 ∧ `policy()` 모드가 `auto`(LIVE 확인 없으면 policy 가 이미 off 를 돌려줌) ∧ 일시정지 아님. 상한·조건(판정자·max_positions·max_weight_pct 등)은 모의와 같다 — 계좌별 상한 없음.
- 스위치 변경은 control_log 에 `channel="web"`, `actor=actor_of(request)`, key `scalp_enabled:<id>` 로 남긴다. 실전 켜기는 플래그 값을 `{"enabled": true, "confirm": <id>, "at": <iso>, "actor": <actor>}` dict 로 저장(읽을 때 dict 면 `enabled` 필드).
- 마이그레이션 추가 없음(control 테이블 재사용). yaml 키 추가 없음(`templates/config/settings.yaml` 의 `scalp.enabled` 주석만 「모의 계좌 기본값」으로).
- 테스트는 실제 claude/codex·주문·osascript 를 실행하지 않는다(러너 주입·monkeypatch).
- 웹 문구: 「단타」, 「켜짐 · 끄기」/「꺼짐 · 켜기」, 「운영 점검」, 「자동매매: 동작 중」, 「자동매매: 전체 일시정지 중 — 새 주문·승인을 내지 않음」, 「전체 자동매매 일시정지 — 계좌 모드와 별개」, 「(전체 일시정지로 주문 멈춤)」. 경로 표기: tray → 「▲ 메뉴」, web → 「웹」, telegram → 「텔레그램」, cli → 「명령줄」, 그 외 원문.
- 종목·계좌 표기 취향: 계좌는 id 그대로(코드) 표시.

## Review Focus

1. 실전 계좌 스위치는 켰는데 LIVE 확인이 없거나 모드가 승인/꺼짐 → 주문 다리가 생기지 않고, 설정 카드에 그 이유가 보여야 한다 (Task 1·2 테스트).
2. 실전 계좌를 켠 뒤 계좌 모드를 승인으로 바꾸면 다음 틱부터 그 계좌는 건너뛴다(스위치는 켜진 채) (Task 1 테스트).
3. `PATCH /settings/scalp/<없는 id 또는 파생 계좌 id>` → 404, 실전 끄기는 confirm 없이 200 (Task 2 테스트).
4. control_log 에 `paused` 기록이 없는데 플래그만 true(옛 버전·CLI 직접) → `GET /system/pause` 는 `at`/`by` null, 화면은 시각 없이 「전체 일시정지 중」만 (Task 3·5 테스트).
5. 트레이 확인창을 띄우지 못함(osascript 없음·예외) → 일시정지는 그대로 진행(비상 정지를 막지 않는다), 사용자가 「취소」 → 아무 변화 없음. 재개는 확인창 없이 지금처럼 (Task 6 테스트).

---

### Task 1: scalp 계좌별 스위치·실효 판정 + 잡·엔진 적용

**Files:**
- Create: `src/trader/features/scalp/switch.py`
- Modify: `src/trader/features/scalp/jobs.py:26-27`, `src/trader/features/scalp/engine.py:1-10(docstring), 103-108(allowed), 222-224(skip_accounts)`, `src/trader/features/scalp/settings.py:13-19(docstring)`
- Test: `tests/features/scalp/test_switch.py`(new), `tests/features/scalp/test_engine.py`, `tests/features/scalp/test_jobs.py`

**Interfaces:**
- Produces (`trader.features.scalp.switch`):
  - `FLAG_PREFIX = "scalp_enabled:"`; `flag_key(account_id: str) -> str`
  - `stored(conn, settings, account) -> tuple[bool, Literal["control","yaml","default"]]` — 플래그 있으면 `(값, "control")`(dict 면 `enabled`), 없으면 모의 `(settings.scalp.enabled, "yaml")`, 실전 `(False, "default")`.
  - `account_enabled(conn, settings, account, port) -> tuple[bool, str | None]` — 거짓이면 사유 문자열: 「꺼짐」, 「실전: 계좌 모드가 자동이 아님(<mode>)」, 「실전: LIVE 확인 없음」(policy note 가 있으면 그 note 사용), 「전체 일시정지」.
  - `any_enabled(conn, settings, port, market: str | None = None) -> bool` — 그 시장을 가진 계좌 중 `stored()` 가 켜진 계좌가 하나라도(실효 판정 전 — 잡이 「꺼짐」과 「계좌가 막힘」을 구분하게).
- Consumes: `port.accounts()`, `port.policy(conn, account)`, `port.paused(conn)`; `trader.kernel.ledger.control.get_flag`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/features/scalp/test_switch.py` (conftest 의 `paper-main`·`paper-two`·`real-1` 계좌, `conn`, `settings`, ports 픽스처 재사용):
  - `test_stored_defaults_paper_follows_yaml_real_off`: yaml enabled=True → paper `(True,"yaml")`, real `(False,"default")`.
  - `test_stored_control_overrides_and_dict_value`: `set_flag(conn,"scalp_enabled:real-1",{"enabled":True,"confirm":"real-1","at":"…","actor":"x"})` → `(True,"control")`; paper 에 `False` → `(False,"control")`.
  - `test_real_enabled_requires_auto_and_live`: real-1 스위치 켬 + LIVE 기록 없음 → `(False, <LIVE 관련 사유>)`; `modes.set_account_mode(... "auto", confirm=...)` 로 LIVE 기록 남긴 뒤 → `(True, None)`; 모드를 `approve` 로 → `(False, "실전: 계좌 모드가 자동이 아님(approve)")`.
  - `test_paused_blocks_all`: paused → paper·real 모두 `(False,"전체 일시정지")`.
- [ ] **Step 2: 실행해 실패 확인** — `uv run pytest tests/features/scalp/test_switch.py -q` → ImportError(switch 없음).
- [ ] **Step 3: `switch.py` 구현** (위 Interfaces 그대로).
- [ ] **Step 4: 엔진·잡 테스트 갱신(RED)**:
  - `test_engine.py::test_allowed_blocks_disabled_paused_bear_and_strategist`: 「모의 계좌 없음」 단언을 `"켜진 계좌 없음"` 포함으로; 「scalp.enabled=false」 단언은 모든 계좌 꺼짐 → `"켜진 계좌 없음"` 으로.
  - 새 `test_real_account_gets_legs_when_switched_on_auto_live`: real-1 스위치 켬 + auto + LIVE → 진입 체크포인트·다리에 real-1 포함. 기존 `test_real_account_never_gets_scalp_legs` 는 「스위치 기본 꺼짐」 이름/설명으로 유지.
  - 새 `test_paper_switch_off_skips_that_account`: paper-two 플래그 False → 다리 없음, paper-main 은 있음.
  - `test_jobs.py`: yaml enabled=False 이지만 paper-main 플래그 True → 틱이 돈다(`skipped` 아님); 모든 계좌 꺼짐 → `{"skipped": "단타 켜진 계좌 없음"}`.
- [ ] **Step 5: 실패 확인** — `uv run pytest tests/features/scalp -q` → 위 테스트 FAIL.
- [ ] **Step 6: 엔진·잡 수정** — `jobs.scalp_body`: `settings.scalp.enabled` 검사 제거, `conn` 연 뒤 `switch.any_enabled(conn, settings, acc, market)` 거짓이면 `{"skipped": "단타 켜진 계좌 없음"}`(일시정지 검사보다 먼저 아니어도 됨 — 순서: 게이트 → 일시정지 → 켜진 계좌). `engine.allowed` 순서: ① `paused` → 「일시정지」(기존 메시지 보존, account_enabled 보다 먼저) ② enabled·모의 계좌 두 검사를 대신해 `if not any(switch.account_enabled(ctx.conn, ctx.settings, a, ctx.ports["accounts"])[0] for a in accounts(ctx) if market in a.markets): return "켜진 계좌 없음"` ③ 이후 기존 검사 그대로. `skip_accounts`: `market not in a.markets or not account_enabled(...)[0]`. 모의 전용 docstring 들을 「계좌별 스위치(실전은 확인·자동·LIVE 필요)」로.
- [ ] **Step 7: 통과 확인** — `uv run pytest tests/features/scalp -q` → 전부 PASS.
- [ ] **Step 8: 커밋** — `feat(scalp): 계좌별 단타 스위치·실효 판정(실전은 자동+LIVE)`

### Task 2: 단타 설정 API + 상태·문구

**Files:**
- Modify: `src/trader/features/scalp/routes.py`(새 `settings_scalp_router`, `ROUTERS` 에 추가; `_paper_account` → 모든 현물 계좌; `/system/scalp` 응답에 `accounts`), `src/trader/features/scalp/status.py`, `src/trader/features/scalp/cli.py:25,130`, `templates/config/settings.yaml:155-167` 주석, `README.md`·`CLAUDE.md:33`·`docs/user-guide.md` 단타 문구, `src/trader/features/scalp/README.md`
- Test: `tests/features/scalp/test_settings_route.py`(new), `tests/features/scalp/test_status.py`, `tests/features/scalp/test_routes.py`

**Interfaces:**
- Consumes: Task 1 `switch.stored`, `switch.account_enabled`, `switch.flag_key`.
- Produces:
  - `GET /settings/scalp` → `{"accounts": [{"id","env","markets","enabled","source","effective","reason"}], "judge": bool, "max_positions": int, "max_weight_pct": float, "default_paper": bool}` (현물 `port.accounts()` 순서).
  - `PATCH /settings/scalp/{account_id}` (WRITE = `[Depends(require_token), Depends(require_same_origin)]`), 본문 `ScalpSwitchBody{enabled: bool | None, confirm: str | None = None}`(`extra="forbid"`) → 같은 GET 모양 응답. 없는 id → 404. 실전 `enabled=True` → `require_confirm(body, id, env)`; 저장값은 Global Constraints 의 dict. `None` → `delete_flag`. 값이 바뀌면 `log_change`.

- [ ] **Step 1: 실패 테스트** — `test_settings_route.py`(scalp `test_system_route.py` 의 앱 픽스처 방식 재사용):
  - `test_get_lists_accounts_with_source_and_effective`: paper-main `source="yaml"`, real-1 `enabled=False, source="default", effective=False, reason="꺼짐"`.
  - `test_patch_requires_token` → 401; 다른 출처 → 403.
  - `test_patch_real_on_requires_confirm`: `{enabled:true}` → 400; `{enabled:true, confirm:"real-1"}` → 200, `get_flag` 이 dict(`confirm=="real-1"`), control_log 1행(key `scalp_enabled:real-1`, channel web).
  - `test_patch_real_off_no_confirm` → 200, 값 False.
  - `test_patch_null_resets_to_default` → 플래그 삭제, `source` yaml/default.
  - `test_patch_unknown_account_404`.
  - `test_patch_rejects_extra_and_non_bool` → 422(`{"enabled":"yes"}`, `{"enabled":true,"x":1}`).
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/features/scalp/test_settings_route.py -q` → 404/FAIL.
- [ ] **Step 3: 라우터 구현** (위 Interfaces).
- [ ] **Step 4: 상태·조각 테스트 갱신(RED)** — `test_status.py`: 절 제목 「단타」, 꺼진 경우 「- 끔 (켜진 계좌 없음)」, 켜진 계좌가 있으면 「- 켬: paper-main」 같은 줄; `test_routes.py`: 실전 계좌에 다리가 있으면 positions 조각에 행이 나온다.
- [ ] **Step 5: 구현** — `status.py` 제목 `"단타"`·계좌별 켬 목록, `routes._paper_account` 의 env 필터 제거(이름 `_spot_account`), `/system/scalp` 에 `accounts`(GET /settings/scalp 와 같은 행) 추가하고 `enabled` 는 `any_enabled`. CLI help 「분 단위 단타(계좌별 켜기 — 실전은 확인·자동·LIVE 필요)」, `cli.py:130` `enabled` 도 `any_enabled`. 문서 문구 교체(`git grep -n "모의 전용" -- README.md CLAUDE.md docs src/trader/features/scalp` 중 단타 관련만 — 파생(fo) 문구는 그대로).
- [ ] **Step 6: 통과 확인** — `uv run pytest tests/features/scalp tests/kernel/web/test_endpoint_map.py -q` → PASS (엔드포인트 지도 테스트가 새 경로를 요구하면 지도 갱신).
- [ ] **Step 7: 커밋** — `feat(scalp): 단타 설정 API(계좌별·실전 확인) + 상태 문구`

### Task 3: 커널 `GET /system/pause`

**Files:**
- Modify: `src/trader/kernel/web/routers/system.py`(새 라우트, `/system/status` 에도 `paused_at`·`paused_by`)
- Test: `tests/kernel/web/test_system.py`

**Interfaces:**
- Produces: `GET /system/pause` → `{"paused": bool, "at": str | None, "by": str | None}` — `paused` 는 control `paused` 플래그(`get_flag(conn,"paused",False)`), `at`·`by` 는 `control_log WHERE key='paused' ORDER BY at DESC, id DESC LIMIT 1` 의 `at`·`channel`, 단 그 행의 `new` 가 현재 값과 다르면 둘 다 null. paused 가 false 면 at/by 는 그 해제 기록(화면은 안 씀).

- [ ] **Step 1: 실패 테스트** — `test_pause_view_reports_latest_log`(tray 로 true 기록 → `{paused:true, at:<at>, by:"tray"}`), `test_pause_view_without_log`(플래그만 true → at/by null), `test_status_includes_paused_at`.
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/kernel/web/test_system.py -q -k pause` → 404 FAIL.
- [ ] **Step 3: 구현** — 내부 도우미 `_pause_info(conn) -> dict` 를 두 라우트가 공유.
- [ ] **Step 4: 통과 확인** — 같은 명령 + `tests/kernel/web/test_endpoint_map.py` → PASS.
- [ ] **Step 5: 커밋** — `feat(web): 일시정지 시각·경로 조회 /system/pause`

### Task 4: 웹 「단타」 설정 카드

**Files:**
- Create: `web/src/screens/system/ScalpSwitch.tsx`
- Modify: `web/src/screens/system/SettingsPanel.tsx`(`<CodexDebate />` 다음에 `<ScalpSwitch />`)
- Test: `web/src/screens/__tests__/scalp-settings.test.tsx`(new, `codex-settings.test.tsx` 모양)

**Interfaces:**
- Consumes: Task 2 GET/PATCH `/settings/scalp`. `useResource(..., ["control","accounts"])`, `patchJson`, `Confirm`(계좌 id 입력 — `Accounts.tsx` PauseControl 의 `live={{accountId}}` 사용 방식 그대로), `Table`, `Chip`.

- [ ] **Step 1: 실패 테스트** —
  - 표에 계좌 id·「모의」/「실전」·스위치(`role="switch"`, `aria-label="<id> 단타"`)·실효(「동작」 또는 사유) 가 그려진다.
  - 모의 스위치 클릭 → `PATCH /settings/scalp/paper-main` 본문 `{enabled:false}` (확인창 없음).
  - 실전 「꺼짐 · 켜기」 클릭 → Confirm 이 열리고, id 입력 후 확인 → 본문 `{enabled:true, confirm:"real-1"}`.
  - 「기본값으로」(source=control 일 때만 보임) → 본문 `{enabled:null}`.
  - 400 응답 → `role="alert"` 에 오류 문구.
- [ ] **Step 2: 실패 확인** — `cd web && npx vitest run src/screens/__tests__/scalp-settings.test.tsx` → FAIL.
- [ ] **Step 3: 구현** — 설명 문구: 「계좌마다 단타를 켜고 끕니다. 실전 계좌는 계좌 id 를 입력해 켜고, 그 계좌가 「자동」 모드이고 LIVE 확인이 된 동안만 돕니다. 상한·조건은 모의와 같습니다(판정자 켬/끔, 최대 보유 n, 종목당 비중 x%).」 출처 표시 「화면에서 설정」/「설정 파일 기본값」/「실전 기본 꺼짐」.
- [ ] **Step 4: 통과 확인** — 같은 명령 PASS, `npx tsc -p . --noEmit` 통과.
- [ ] **Step 5: 커밋** — `feat(web): 설정 화면 단타 계좌별 스위치`

### Task 5: 헬스·대시보드·계좌 화면 일시정지 표기

**Files:**
- Create: `web/src/components/PauseNotice.tsx` (`usePause()` 훅 + `PauseBanner` + `pauseByLabel(by)`)
- Modify: `web/src/screens/system/Health.tsx:66-80`(두 줄 머리), `web/src/screens/today/Today.tsx`(맨 위 `PauseBanner`), `web/src/screens/today/LaneCards.tsx`(paused 배지 `title`/Tooltip 「전체 자동매매 일시정지 — 계좌 모드와 별개」), `web/src/screens/accounts/Accounts.tsx:36-46,150-152`·`Account.tsx`(모드 옆 「(전체 일시정지로 주문 멈춤)」 — paused 일 때만)
- Test: `web/src/screens/__tests__/system.test.tsx`, `today.test.tsx`, `accounts.test.tsx`, `account.test.tsx`

**Interfaces:**
- Consumes: Task 3 `GET /system/pause` (`useResource(..., ["control","accounts"])`).
- Produces: `PauseBanner` — paused 일 때만: 「자동매매 전체 일시정지 중 · <fmtWhen(at)> <경로> — 계좌 모드(자동/승인)는 그대로, 새 주문만 멈춤」 + 링크 「재개」(`#/accounts` — 기존 재개 버튼이 실전 confirm 을 처리). at 없으면 시각·경로 부분 생략.

- [ ] **Step 1: 실패 테스트** —
  - Health: 「운영 점검: 정상」 텍스트와 「N개 영역」, paused=false 면 「자동매매: 동작 중」, paused=true + at/by(tray) 면 「자동매매: 전체 일시정지 중 — 새 주문·승인을 내지 않음」·「▲ 메뉴」·「재개」 링크.
  - Today: `/system/pause` paused=true → 배너 문구, false → 배너 없음; at null → 「전체 일시정지 중」 있고 「▲ 메뉴」 없음.
  - LaneCards: paused 카드 배지에 「계좌 모드와 별개」 설명 노출(title 또는 툴팁 텍스트).
  - Accounts/Account: paused 행의 모드 옆 「(전체 일시정지로 주문 멈춤)」, 아닐 땐 없음.
- [ ] **Step 2: 실패 확인** — `cd web && npx vitest run src/screens/__tests__/system.test.tsx src/screens/__tests__/today.test.tsx src/screens/__tests__/accounts.test.tsx src/screens/__tests__/account.test.tsx` → 새 단언 FAIL.
- [ ] **Step 3: 구현** — 헬스 두 줄: 첫 줄 `운영 점검: {LEVEL_TEXT} — 데몬·잡·주문·수집 등 {areas.length}개 영역{문제 영역 수>0 ? ` 중 n개 주의` : " 문제없음"}` + 기존 점검 시각·버튼, 둘째 줄 자동매매 상태(기존 「일시정지」 칩 대체).
- [ ] **Step 4: 통과 확인** — 같은 명령 PASS + `npx vitest run` 전체 + `npx tsc -p . --noEmit` + `npm run build`.
- [ ] **Step 5: 커밋** — `feat(web): 일시정지와 운영 점검·계좌 모드 구분 표기`

### Task 6: ▲ 메뉴 일시정지 확인창·구분선

**Files:**
- Create: `src/trader/kernel/platform/confirm_dialog.py`
- Modify: `src/trader/kernel/platform/tray.py`(`TrayController.pause_action` 에서 켜기 직전 확인, `run_tray` 메뉴에서 `toggle_pause` 뒤 `pystray.Menu.SEPARATOR`), 모듈 docstring
- Test: `tests/kernel/platform/test_confirm_dialog.py`(new), `tests/kernel/platform/test_tray.py`

**Interfaces:**
- Produces: `ask_yes_no(title: str, message: str, *, platform: str = sys.platform, run: Callable = subprocess.run, message_box: Callable | None = None) -> bool | None` — True=확인, False=취소, None=띄우지 못함. macOS: `run(["osascript","-e", <display dialog … buttons {"취소","일시정지"} default button "취소" cancel button "취소" with icon caution>], capture_output=True, text=True, timeout=120)` 종료코드 0 → True, 1(사용자 취소) → False, 그 외·예외 → None. Windows: `message_box` 기본 `ctypes.windll.user32.MessageBoxW(0, message, title, MB_YESNO|MB_ICONWARNING|MB_DEFBUTTON2|MB_TOPMOST)` → IDYES(6) True, IDNO False, 예외 None. 기타 플랫폼 None.
- `TrayController.__init__` 에 `confirm_pause: Callable[[], bool | None] | None = None` 추가. `pause_action` 워커: 현재 캐시 상태가 일시정지 아님(=켜려는 중)이고 `confirm_pause` 가 있으면 호출 — False 면 아무것도 안 함, True·None 이면 토글. 재개는 확인 없이.
- 문구: 제목 「trader」, 본문 「자동매매를 전체 일시정지할까요? 새 주문·승인이 멈춥니다(계좌 모드는 그대로). 앱을 끄려면 「종료」를 누르세요.」

- [ ] **Step 1: 실패 테스트** — `test_confirm_dialog.py`: 가짜 `run` 이 returncode 0/1/2·`FileNotFoundError` → True/False/None/None, 인자에 `osascript` 와 본문 포함; Windows 가짜 `message_box` 6/7/예외 → True/False/None; `platform="linux"` → None. `test_tray.py`: `TrayController(confirm_pause=lambda: False)` + 일시정지 아님 → `on_toggle_pause` 미호출; `True`·`None` → 호출; 이미 일시정지(재개) → confirm 미호출·토글 호출. (워커 스레드는 기존 테스트가 기다리는 방식 그대로.)
- [ ] **Step 2: 실패 확인** — `uv run pytest tests/kernel/platform/test_confirm_dialog.py tests/kernel/platform/test_tray.py -q` → FAIL.
- [ ] **Step 3: 구현** — `run_tray` 는 `TrayController(..., confirm_pause=lambda: ask_yes_no("trader", <본문>))` 로 연결, `toggle_pause` 항목 뒤 SEPARATOR.
- [ ] **Step 4: 통과 확인** — 같은 명령 PASS.
- [ ] **Step 5: 커밋** — `feat(tray): 일시정지 전 확인창·메뉴 구분선`

### Task 7: 문서·전체 검증

**Files:** `docs/user-guide.md`(단타 켜기·일시정지 표기 절), `CHANGELOG.md` 가 있으면 항목.

- [ ] **Step 1:** 사용 안내에 「시스템 › 관리 › 설정 › 단타」 사용법(실전 확인·자동·LIVE 조건), 헬스 두 줄의 뜻, 일시정지 띠·▲ 메뉴 확인창을 적는다.
- [ ] **Step 2:** `uv run ruff check . && uv run ruff format --check .` → 통과.
- [ ] **Step 3:** `uv run pytest -q` → 전부 PASS(알려진 플레이크는 따로 표기).
- [ ] **Step 4:** `cd web && npx vitest run && npx tsc -p . --noEmit && npm run build` → 통과.
- [ ] **Step 5: 커밋** — `docs: 단타 계좌별 켜기·일시정지 표기 안내`
