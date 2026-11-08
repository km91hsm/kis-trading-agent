# 전체 일시정지 중에도 위험 청산은 계속 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전체 일시정지(control `paused`)가 새 매수·진입·판단 매도·승인 실행만 막고, 위험 청산(현물 손절, 단타 청산 전부, 파생 손절·만기·강제·보유기간·장 마감 청산·축소·대기 진입 회수)은 계속 나가게 한다.

**Architecture:** 커널 `ExecPolicy` 에 일반 필드 `risk_exit` 를 더해, 위험 청산으로 표시된 실행은 `paused` 를 보지 않는다(mode off·dry_run 은 그대로). 현물 실행기는 origin 과 action 으로 위험 청산을 표시한다. 단타·파생은 잡이 일시정지에서 틱을 건너뛰지 않게 하고, 청산 경로의 일시정지 검사를 빼며, 진입 경로는 그대로 막는다.

**Tech Stack:** Python(pytest·ruff, uv), React + vitest(문구).

**Spec:** `docs/superpowers/specs/2026-11-09-pause-allows-risk-exits-design.md`
**Investigation (where paused blocks today, with file:line and affected tests):** `docs/superpowers/plans/2026-11-09-pause-allows-risk-exits-investigation.md`

## Global Constraints

- 위험 청산 = 현물 `origin=="stop"` 의 SELL, 단타 `origin=="scalp"` 의 SELL(청산·이월 청산), 파생 축소 다리(`reduce`·`continue_reduce`·`recall_entries`, rules 의 reduce 판정)와 파생 engine 의 자동 청산(손절·만기·강제·보유기간·장 마감·RESIZE_DOWN).
- 판단 매도(분석 SELL/TRIM, 재평가 세션, 리밸런스 TRIM, 헤지·파킹 해제, 재호가, 파생 재평가 CLOSE/REPLACE)와 **승인 실행**(`core.approve`, 매수·매도 모두)은 일시정지 중 그대로 막는다.
- `mode == "off"` 계좌와 `dry_run` 은 위험 청산이어도 전송하지 않는다(변경 없음). approve 모드 계좌의 손절이 승인 요청이 되는 기존 동작도 그대로.
- 커널은 기능 이름을 모른다: `ExecPolicy.risk_exit: bool = False` 같은 일반 필드만. `transmits = mode != "off" and not dry_run and (risk_exit or not paused)`.
- 진입은 계속 막는다: 단타 `engine.allowed()` 의 「일시정지」, 파생 진입 다리 record_only, 현물 BUY/ADD.
- 일시정지 중 실제로 위험 청산이 나가면 알림은 「일시정지 중이지만 손절(청산)을 실행했습니다」 취지로(기존 `stop_paused` 「막힘」 알림 대체).
- 문구: 헬스 「자동매매: 전체 일시정지 중 — 새 매수·진입과 승인을 멈춤 · 손절·청산은 계속」, 대시보드 띠 「자동매매 전체 일시정지 중 · <시각> <경로> — 계좌 모드(자동/승인)는 그대로, 새 매수·진입만 멈춤(손절·청산은 계속)」, 계좌 보조 표기 「(전체 일시정지로 새 매수 멈춤)」, ▲ 메뉴 확인창 「자동매매를 전체 일시정지할까요? 새 매수·진입과 승인이 멈춥니다(손절·청산은 계속, 계좌 모드는 그대로). 앱을 끄려면 「종료」를 누르세요.」
- CLAUDE.md `paused` 불변식: 「`control` 의 `paused` 가 켜지면 신규 진입·판단 매도·승인 실행이 멈춘다. 위험 청산(현물 손절·단타 청산·파생 손절/만기/축소)은 계속된다(`ExecPolicy.risk_exit`).」
- 테스트는 실제 주문·claude·codex 를 실행하지 않는다. NEVER `git stash`.

## Review Focus

1. 일시정지 + 현물 분석 SELL(origin 없음/analyze) → 전송 안 됨; 일시정지 + 손절 → 전송됨 (Task 1·2).
2. 일시정지 + 승인 대기 SELL 승인 실행 → 계속 pending (Task 1 기존 테스트 유지).
3. 일시정지 + mode off 계좌 손절 → 전송 안 됨, dry_run 손절 → 전송 안 됨 (Task 1).
4. 일시정지 + 단타 신호 → 새 BUY 없음, 열린 다리 손절 → SELL (Task 3).
5. 일시정지 + 파생 손절 조건 → 축소 주문 전송, 같은 틱의 새 진입은 기록만; 실제 청산이 나간 뒤 정산 전이가 막히지 않음 (Task 4).

---

### Task 1: 커널 ExecPolicy.risk_exit + 현물 실행기 표시

**Files:** Modify `src/trader/kernel/execution/types.py`(ExecPolicy), `src/trader/features/spot/executor.py`(~975 정책 생성; `policy_of` ~1315 승인 경로는 risk_exit=False 유지). Test `tests/kernel/execution/test_types.py`, `tests/features/spot/test_executor.py`.
**Produces:** `ExecPolicy(mode, note, paused, dry_run, risk_exit=False)`; `.transmits` 규칙은 Global Constraints. 현물 실행기: `risk_exit = decision action in ("SELL",) and origin.origin in ("stop", "scalp")` (TRIM 등 판단 매도는 False).

- [ ] Step 1: 실패 테스트 — `test_policy_transmits_risk_exit_ignores_paused`(paused+risk_exit → True; +off → False; +dry_run → False; risk_exit 없이 paused → False), 실행기 `test_paused_stop_loss_sell_transmits`(origin stop SELL → 주문 1건), `test_paused_scalp_exit_sell_transmits`, `test_paused_scalp_buy_blocked`, `test_paused_analyze_sell_blocked`(origin 기본 SELL → 주문 없음). 기존 `test_paused_blocks_orders_and_approval_requests`·`test_paused_blocks_approve`·`test_policy_without_paused_is_fail_closed`·core `test_approve_paused_or_mode_changed_keeps_pending` 유지.
- [ ] Step 2: RED 확인 `uv run pytest tests/kernel/execution tests/features/spot/test_executor.py -q -p no:cacheprovider`.
- [ ] Step 3: 구현. ExecPolicy 필드는 기본값을 둬 다른 생성자(fo `record_only` 등)는 그대로 동작.
- [ ] Step 4: GREEN.
- [ ] Step 5: 커밋 `feat(execution): 위험 청산은 일시정지와 무관하게 전송(ExecPolicy.risk_exit)`.

### Task 2: 현물 손절 — 일시정지 중에도 실행

**Files:** Modify `src/trader/features/spot/monitor.py`(`_sell_now` ~555-605: `live` 에서 paused 제외, paused 분기 제거·알림 대체), `src/trader/kernel/notify/messages.py` 또는 spot messages 의 `stop_paused` → 새 안내 문구(위치는 기존 정의를 따른다), docstring(monitor.py 머리 「paused·세션 상한이면 기록만」 → 재평가에만 해당). Test `tests/features/spot/test_monitor.py`, `tests/features/spot/test_jobs.py`.
**Consumes:** Task 1 (`origin stop` 이 paused 에서도 전송).

- [ ] Step 1: 실패 테스트(기존 것을 고쳐 씀) — `test_paused_stop_loss_notifies_once` → `test_paused_stop_loss_still_sells_and_notifies`(paused 에서 손절 SELL 1건 + 안내 1회), `test_stop_loss_does_not_cancel_resting_sell_when_paused_or_off` → paused 는 취소 후 손절, off 는 그대로 취소 안 함, `test_paused_tick_records_trigger_notifies_once_and_places_no_order` → 재평가 트리거는 기록만·주문 없음은 유지(손절 아닌 트리거), `test_reevaluation_blocked_when_paused_or_cap` 유지, `tests/features/spot/test_jobs.py::test_paused_daemon_monitor_tick_notifies_stop_paused_once` → 손절 실행·안내.
- [ ] Step 2: RED `uv run pytest tests/features/spot/test_monitor.py tests/features/spot/test_jobs.py -q -p no:cacheprovider`.
- [ ] Step 3: 구현. 안내 알림은 하루 1회 플래그(기존 `sl_paused_notified:*` 재사용 가능)로 실제 손절 접수 시에만.
- [ ] Step 4: GREEN + `uv run pytest tests/features/spot -q -p no:cacheprovider`(hedge·rebalance·approvals 의 paused 테스트는 그대로 통과해야 한다).
- [ ] Step 5: 커밋 `feat(spot): 일시정지 중에도 손절 매도`.

### Task 3: 단타 — 일시정지 중에도 청산

**Files:** Modify `src/trader/features/scalp/jobs.py`(~60 paused skip 제거 — 창·휴장 게이트와 「켜진 계좌/상태 없음」 skip 은 유지), `src/trader/features/scalp/exits.py`(`can_trade` ~188: paused 조건 제거, docstring), `src/trader/features/scalp/engine.py` tick docstring(~592-596). Test `tests/features/scalp/test_engine_exit.py`, `tests/features/scalp/test_jobs.py`.
**Consumes:** Task 1(scalp SELL 은 risk_exit).

- [ ] Step 1: 실패 테스트 — `test_paused_does_not_drop_open_position` → `test_paused_still_exits_open_position`(paused + 손절가 아래 → SELL), `test_paused_blocks_new_scalp_entry`(paused + 진입 신호 → BUY 없음, `allowed()` 「일시정지」), `test_jobs.py`: paused 이면 skipped 가 아니라 틱이 돈다(상태가 있을 때), 상태·켜진 계좌 둘 다 없으면 여전히 skipped. 기존 `test_switch.py::test_paused_blocks_all`·`test_engine.py::test_allowed_blocks_disabled_paused_bear_and_strategist` 유지.
- [ ] Step 2: RED `uv run pytest tests/features/scalp -q -p no:cacheprovider`.
- [ ] Step 3: 구현.
- [ ] Step 4: GREEN(알려진 env 실패 `test_judge_for_uses_fake_under_fake_claude` 제외).
- [ ] Step 5: 커밋 `feat(scalp): 일시정지 중에도 단타 청산·이월 청산`.

### Task 4: 파생 — 일시정지 중에도 손절·축소

**Files:** Modify `src/trader/features/fo/jobs.py`(`monitor_job` ~187-193: paused 로는 skip 하지 않음 — `_day_skip` 에 paused 무시 옵션 또는 별도 판정; `session_job`·`reconcile_job`·`reevaluate_request` 는 그대로), `src/trader/features/fo/executor.py`(`record_only` ~346 에 `risk_exit` 전달, `reduce` ~1140·`continue_reduce` ~1317·`recall_entries` ~1000 은 위험 청산, `run_leg` ~703 은 진입이면 기존, CLOSE/REPLACE 축소면 그 경로가 재평가 판단에서 왔는지 엔진 자동 청산에서 왔는지 구분 — 재평가 유래는 막음), `src/trader/features/fo/rules.py`(`evaluate` ~222: reduce 다리는 paused 로 「기록만」 하지 않음), `src/trader/features/fo/engine.py`(`_hold`·`NO_STATE_CHANGE` ~73/95/458/494/513/544/555/694: paused 가 위험 청산·정산 전이를 막지 않게, 진입 대기는 그대로 보류), fo README·docstring. Test `tests/features/fo/test_jobs.py`, `test_executor.py`, `test_review_s4.py`, `test_review_s4_github.py`, `test_review_s5.py`, `test_rules.py`, 해당 engine 테스트.
**Consumes:** Task 1.

- [ ] Step 1: 조사 문서의 파생 절과 H 절의 파생 테스트 목록을 읽고, 각 기존 단언을 「위험 청산은 진행 / 진입·재평가는 그대로」로 다시 쓴 실패 테스트: `monitor_job` paused 에서 틱 실행, paused + 손절 조건 → reduce 주문 전송, paused + 진입 신호 → 기록만(`held: "paused"` 유지), paused + 재평가 CLOSE → 기록만(판단), 실제 청산 후 `awaiting_settlement` 전이가 paused 로 막히지 않음, `test_paused_engine_consumes_nothing` 은 진입 쪽 단언만 남기고 reduce 쪽은 전송으로.
- [ ] Step 2: RED `uv run pytest tests/features/fo -q -p no:cacheprovider`.
- [ ] Step 3: 구현. 판단이 모호한 경로(예: run_leg 의 CLOSE 가 어느 쪽인지)는 Global Constraints 의 정의(engine 자동 청산 = 위험, 재평가 = 판단)로 정하고 보고서에 근거를 남긴다.
- [ ] Step 4: GREEN.
- [ ] Step 5: 커밋 `feat(fo): 일시정지 중에도 파생 손절·축소`.

### Task 5: 문구·문서

**Files:** `web/src/screens/system/Health.tsx`, `web/src/components/PauseNotice.tsx`, `web/src/screens/accounts/Accounts.tsx`·`Account.tsx`, `web/src/screens/today/LaneCards.tsx`(설명 문구), 해당 vitest; `src/trader/kernel/platform/tray.py`(`PAUSE_CONFIRM_MESSAGE`) + test; `CLAUDE.md`(paused 불변식), `docs/user-guide.md`, `docs/architecture.md`(Ruling 20 문단), `src/trader/features/accounts/gates.py:3-4`, spot·scalp·fo README 의 paused 문장, `src/trader/features/spot/hedge.py:5`·`cash_park.py:13` 은 판단 매도라 그대로인지 확인.

- [ ] Step 1: 문구 테스트 갱신(RED) — vitest 의 일시정지 문구 단언, tray 확인창 문구 단언.
- [ ] Step 2: RED `cd web && npx vitest run` / `uv run pytest tests/kernel/platform -q -p no:cacheprovider`.
- [ ] Step 3: 문구·문서 교체(Global Constraints 문구 그대로).
- [ ] Step 4: GREEN + `npx tsc -p . --noEmit` + `npm run build` + `uv run ruff check src tests`.
- [ ] Step 5: 커밋 `docs: 일시정지는 새 매수만 멈추고 손절·청산은 계속`.
