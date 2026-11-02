# M4 — kernel/session + kernel/execution + features/spot analyze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 옛 저장소의 LLM 세션 러너(`orchestrator/session.py`·`agents/*`·`usage.py`), 주문 실행기(`orchestrator/executor.py`·승인 지문), 도구 표면(`orchestrator/tools.py` 현물 부분·`summary.py`), 리스크 게이트(`risk/{rules,stance,stops,horizon,costs,tradability}.py`)를 **동작 보존**으로 `kernel/session`·`kernel/execution`·`kernel/lane`·`features/spot` 에 옮기고, 옛 테스트(`tests/orchestrator/test_session.py` 33, `test_tools.py` 중 44, `test_executor.py` 중 55, `test_approval_fingerprint.py` 4, `test_summary.py` 14, `tests/agents/test_registry.py` 10·`test_schemas.py` 7·`test_anonymize.py` 5, `tests/risk/test_rules.py` 41·`test_stops.py` 5·`test_horizon.py` 4·`test_stock_lessons.py` 중 4, `tests/test_usage.py` 11, `test_cli_analyze.py` 7, `test_cli_approvals.py` 3, `tests/notify/test_messages.py` 중 5, `test_stock_regression_fo.py` 2)를 새 위치로 이식해 통과시킨다. 완료 기준은 `FAKE_CLAUDE=1 uv run trader spot analyze --ticker 005930 --dry-run` 이 옛 저장소와 같은 체크포인트 집합(`00_inputs, 10_analyst_{technical,fundamental,news_macro,sentiment}, 20_debate_r0_{bull,bear}, 20_debate_r1_{bull,bear}, 25_research_summary, 30_decision, 40_risk_check_<계좌>`)을 쓰는 것과 골든 회귀 스냅샷 1회 재기준이다.

**Architecture:** 네 덩이, 의존 방향은 `kernel` ← `accounts`·`ingest`(포트 제공) ← `spot`(레인, 기능 import 0). ① 커널 계약 확장(Task 1): `AgentDef`·`Port`·`SessionKind.stdin_extra`·`StageRule.min_counts`·`ToolCommand.handler`(Typer 허용)·`Feature.agents/ports`, 계약 검사(에이전트 이름·포트 중복, `tier=="lane"` ↔ `LANES`, `origins ⊆ ORIGIN_CHECK`), 커널 설정 섹션 `settings.llm/models/claude_bin/anonymize`, `Boot.ports`, `kernel/lane/`(`Lane` 프로토콜·`LaneContext`·`LANES`), 루트 `trader tool` 명령(기능 `tools` 를 모아 마운트). ② `kernel/session/`(Task 2~4): 원장 v4(`agent_calls`·`llm_rate_limits`), `calls.py`(세션 행·상한), `usage.py`(사용량 감속), `agents.py`(md 로더·`agents_json`·`orchestrator_prompt`), `anonymize.py`, `runner.py`(`SessionSpec`·`run_session(spec, kind, ...)` — 종류 분기는 전부 `SessionKind` 항목에서 읽음). ③ `kernel/execution/`(Task 5~6): 원장 v5(`orders`·`approvals`·`risk_checks`·`exec_locks`·`buy_reservations`·`positions_snapshot`·`account_nav`), `types.py`(`OrderIntent`·`ExecPolicy`·`OrderBookkeepingError`), `idem.py`(`IdemStore` 프로토콜 + `ExecKeyStore`), `ledger.py`(주문·승인·스냅샷·집계 원시), `core.py`(`transmit`·`request_approval`·`approve`·`reject`·`expire_approvals`·`approval_fingerprint`), `ports.py`(`AccountsPort`·`InputsPort` 프로토콜), `notify.messages` 에 주문·승인 문구. ④ `features/spot/`(Task 7~14): 설정(`risk`·`debate_rounds`·`debate`), 스키마, 원장 v6(`decisions`·`decision_rounds`·`analyst_reports`·`debate_turns`·`ticker_names`·`ticker_exchanges`), 게이트(`rules`·`stance`·`stops`·`horizon`·`costs`·`tradability`), `summary`, `tools`(StageRule 표 + `trader tool …`), `executor`(옛 플래그 → `(origin, stage)` 표, 커널 코어 호출), `fake`·`agents/*.md`·SessionKind `analyze`·`trader spot analyze`, 승인 CLI·텔레그램·만료 잡, 골든 회귀. `accounts`·`ingest` 는 `ports.py` 로 `AccountsPort`·`InputsPort` 구현을 등록하고 spot 은 `Boot.ports` 로만 받는다.

**Tech Stack:** Python 3.12, pydantic v2(`extra="forbid"`), sqlite3(WAL, BEGIN IMMEDIATE), typer, `claude -p --output-format stream-json`(subprocess, 테스트는 runner 주입), FakeBroker/FakeMarket(커널 broker.fake).

**Spec:** `docs/superpowers/specs/2026-11-02-kis-trading-agent-replatform-design.md` §2.1(`kernel/session`·`kernel/execution`·`kernel/lane`·`features/spot` 행), §2.2(Lane 추상·공유 타입·SessionKind 레지스트리·알려진 불일치 보존), §2.3(`tools`+`StageRule`, `origins`, `session_kinds`·`agents`, `deps`), §2.4(decisions+decision_rounds, approvals fingerprint NOT NULL, orders origin CHECK, exec_locks+buy_reservations, positions_snapshot+account_nav), §2.6(규칙의 코드화), §2.7-1(레인은 기능 import 금지 → deps 주입), §3 M4 행, 「검증」 동등성 줄. 옛 규칙 원문: `../trading-agent/CLAUDE.md` 「아키텍처 핵심 규칙」의 「LLM 은 도구만 쓴다」「서브에이전트 정의」「순서는 도구 전제조건이 강제한다」「체크포인트가 상태의 원본」「수렴 감시」「사용량 감속」「세션·비용」「계좌 모드」「주문은 executor 안에서만」「dry_run 은 env」「실전 auto 는 확인 기록」「리스크 규칙은 순수 함수」「승인은 30분 만료 1회」「paused」「`claude -p` 플래그」. 인벤토리(세션 스크래치, 참고): `/private/tmp/claude-501/-Users-ohhyeonseok-LLM/1a79462d-ce8a-46a0-b1c4-de0d70ffcf19/scratchpad/inv-{session,execution,risk}.md`.

## Global Constraints

- 옛 저장소는 `../trading-agent`. 옛 `secrets/`·`config/`·`state/` 와 이 저장소 `secrets/` 는 읽지 않는다. CLI 실기는 scratch `TRADER_HOME` 에서만. **`trader spot analyze` 는 `FAKE_CLAUDE=1` 없이 돌리지 않는다**(구독 호출). `claude` 실행파일은 테스트에서 부르지 않는다(`runner=` 주입).
- **동작 보존.** 옛 테스트의 단언을 바꾸지 않는다(import 경로·fixture·생성자·설정 객체·CLI 경로·테이블 이름만 바꾼다). 바꿔야 하는 단언은 「바꾸는 옛 단언」 표에 있는 것만(없으면 표에 추가하고 PR 본문에 적는다). 알려진 불일치(`_STAGE_KIND` 가 `51_order`·`55_order_sl` 만 맞음, `fo.session_cap`·`decision_trace` 누락, `ask_json` 의 감속 우회)는 고치지 않고 `features/spot/README.md` 「알려진 차이」에 적는다.
- import 3층: `kernel` 은 `trader.features` 를 import 하지 않는다(`discover` 제외). **`features/spot` 은 `trader.kernel.*` 만 import 한다**(accounts·ingest 포함 어떤 기능도 금지, 함수 안 지연 import 도 금지) — 계좌·브로커·모드·수집은 `Boot.ports["accounts"]`·`Boot.ports["inputs"]` 로 받는다. `accounts`·`ingest` 는 커널 + 서로의 `api.py` 만. `tests/kernel/registry/test_import_boundary.py::test_real_tree_has_no_violations` 가 강제한다.
- 커널 파일 수정은 Task 1·2·5·6 이 명시한 것만. 이후 Task 에서 커널을 고쳐야 하면 ledger 에 Ruling 으로 적고 최소로.
- 경로 `platform.paths`, 시각 `config.time.KST`·`MARKET_TZ`, 저장 시각 UTC ISO8601(`+00:00`), 파일명 `:` 금지, 모든 텍스트 파일 I/O(`read_text/write_text/open`, 테스트 포함)에 `encoding="utf-8"`, subprocess `shell=False, encoding="utf-8", errors="replace"`. 로거 `trader.session`·`trader.execution`·`trader.spot`. 사용자 대면 문구·docstring 한국어.
- 시크릿·계좌 값은 로그·stdout·예외에 넣지 않는다. 승인·주문 알림은 계좌 **id** 만 쓴다.
- `claude -p` 인자는 옛 것 그대로: `-p --output-format stream-json --verbose --model <models.orchestrator|sonnet> --tools Bash,Agent --allowedTools "Bash(<tool_command()> *)" --permission-mode dontAsk --setting-sources "" --disable-slash-commands --agents <agents.json> --append-system-prompt <prompt> --session-id <uuid4> --max-budget-usd <llm.budget_usd>`. `--bare`·`--max-turns` 금지. env `CLAUDE_CODE_RETRY_WATCHDOG=1`·`TRADER_DRY_RUN`("1"|"0")·`TRADER_HOME`·`PATH=tool_bin_dir + pathsep + child_path(bin)`. cwd 는 `tempfile.mkdtemp(prefix="trader-claude-")` 뒤 삭제. `tool` 서브프로세스는 `TRADER_DRY_RUN` 이 없으면 dry_run 이다.
- 옛 값 그대로: `APPROVAL_TTL = 30분`, `EXEC_KEY_STALE = 10분`, `REQUOTE_AFTER_S = 600`, `MIN_REPORTS = 2`, `BUNDLE_LIMIT = 24_000`(바이트), `TERM_GRACE_S = 10.0`, `KILL_WAIT_S = 5.0`, `CANCEL_POLL_S = 1.0`, `CONVERGENCE_GAP = 0.2`, `MIN_HOLD_TRADING_DAYS = 3`, `MAX_TRADES_PER_WEEK = 2`, `LOW_CONFIDENCE = 0.5`, `COST_MULTIPLE = 2`, `_ROUND_TRIP_BP = {"KR": 30, "US": 50}`, stance 상수 7개, `SHORT_MAX_DAYS = 10`·`MID_MAX_DAYS = 40`·`PERIODIC_DAILY_CAP = 6`, `WINDOWS = {"h5": 5h, "d7": 7d}`, `RESUME_KINDS = {"resume", "fo_resume"}`, `THROTTLE_FLAG = "usage_throttle"`, `CHECKPOINT_SCHEMA_VERSION = 1`, 설정 기본값(`llm.budget_usd 5.0`, `timeout_s 900`, `timeout_by_kind {screen:300, discover:300, resume:600, fo_resume:600, reflect:600}`, `timeout_retry_max 2`, `daily_session_cap 80`, `market_session_cap 2`, `usage_throttle {enabled, soft 0.80, hard 0.95, max_age_h 6.0}`, `usage_file "~/.claude/rate_limits.json"`, `usage_probe {enabled, interval_min 30, skip_if_recent_min 5}`, `models {}`, `debate_rounds 1`, `debate {skip_on_consensus true, consensus_min_conviction 0.7}`, `risk` 13키 옛 기본값·`horizon_profiles` 3종 필수).
- 원장 v1 재설계는 스펙 §2.4 그대로: 금액 TEXT(Decimal), `orders.origin CHECK IN ('llm','stop','hedge','scalp','rebalance','reprice','park','external','fo')`, `orders.asset CHECK IN ('stock','fo')`, `orders.status CHECK IN ('accepted','rejected','failed','unknown','filled','partial','cancelled','expired')`, `approvals.fingerprint TEXT NOT NULL`, `exec_locks` + `buy_reservations` 분리, `decisions`(하루 1행, UNIQUE(date, market, ticker)) + `decision_rounds`(UNIQUE(decision_id, round)), `positions_snapshot(asset 열)` + `account_nav`. 마이그레이션 버전: v4 `session`(커널), v5 `execution`(커널), v6 `spot`.
- `templates/config/settings.yaml` 에 새 키(`llm`·`models`·`claude_bin`·`anonymize`·`risk`·`debate_rounds`·`debate`)를 옛 템플릿 값·주석 그대로 추가한다(자리표시자만; `tests/kernel/config/test_templates.py` 통과).
- 에이전트 md 8개(`orchestrator, analyst-technical, analyst-fundamental, analyst-news-macro, analyst-sentiment, researcher-bull, researcher-bear, trader`)는 옛 `../trading-agent/.claude/agents/` 에서 **본문 그대로** `src/trader/features/spot/agents/` 로 복사한다. `orchestrator.md` 만 `## resume 모드`…`## screen 모드` 절 7개를 지운다(M5~M10 이 되살림). 「공통 규칙」 6문장은 글자 하나도 바꾸지 않는다.
- 테스트는 `project`·`conn`·`tmp_path` fixture 만 쓰고 네트워크·`claude` 는 막는다(runner 주입, FakeBroker·FakeMarket). spot 테스트의 설정 객체는 `tests/features/spot/conftest.py::settings` fixture(기본값 로드)로 받는다.
- 브랜치 `feat/m4-session-execution`, PR 대상 main, 커밋 트레일러 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, 작성자는 저장소 `git config user.email`(noreply). ruff line-length 100. CI ubuntu+windows 녹색(프로세스 그룹 테스트는 posix 전용 skip). `CLAUDE.md` ≤ 16KB. 새 의존성 없음.

## Review Focus

스펙이 암시하지만 옛 테스트가 덮지 않는 입력. 각 줄의 테스트는 해당 Task 에 있다.

1. `TRADER_DRY_RUN` 이 env 에 **없을 때** `trader tool submit-decision` 은 dry_run 으로 동작해 주문·승인 요청을 내지 않는다(LLM 이 플래그를 빠뜨려도 주문 없음). → Task 10
2. `exec_locks` 를 잡은 뒤 `orders` 행 기록이 실패하면 키는 `sent_at` 이 찍힌 채 남아 **다른 프로세스가 넘겨받지 못하고**, `trader spot exec-key release` 로만 풀린다(접수된 주문의 중복 발주 방지). → Task 5·11
3. `settings.llm.timeout_by_kind` 에 없는 kind 는 `timeout_s` 로 폴백하고, `SessionKind.timeout_key` 가 그 dict 의 키다(새 레인이 kind 를 더해도 커널이 모름). → Task 4
4. 승인 `approve` 는 요청 뒤 계좌 모드가 `off` 로 내려갔거나 `paused` 가 켜졌으면 실행하지 않고 요청은 pending 으로 남긴다(웹·텔레그램·CLI 어느 경로든). → Task 6·13
5. `trader spot analyze --market US` 는 `--exchange` 없이 exit 1 이고 `state/runs/` 아래에 디렉터리를 만들지 않는다. → Task 12

---

## 파일 구조

```
templates/config/settings.yaml           + llm, models, claude_bin, anonymize, risk, debate_rounds, debate
src/trader/kernel/
  registry/types.py                      + AgentDef, Port; SessionKind.stdin_extra; StageRule.min_counts; ToolCommand.handler Typer|Callable; Feature.agents/ports
  registry/contracts.py                  LANES → kernel/lane 로 이동(re-export), 에이전트·포트 중복, lane↔LANES, origins ⊆ ORIGIN_CHECK
  config/__init__.py                     + LlmSettings, UsageThrottle, UsageProbe; KERNEL_SECTIONS += llm, models, claude_bin, anonymize
  cli/boot.py                            Boot.ports; boot(ports=True)
  cli/main.py                            + `tool` 루트(kernel/cli/tool_cmd.py), build_daemon_deps 변경 없음
  cli/tool_cmd.py                        신규: Feature.tools → `trader tool <name>` 마운트, JSON 1줄 출력 규약 헬퍼
  lane/__init__.py, lane/types.py        신규: LANES, Lane(Protocol), LaneContext, WorkItem
  ledger/migrations.py                   KERNEL += SESSION(v4), EXECUTION(v5)   (각 모듈이 정의, 여기서 합침)
  session/migrations.py                  신규: SESSION = Migration(4, "session", owner="kernel/session")
  session/calls.py                       신규 ← session._start_call/_finish_call, tools.sessions_today, quick.calls_today
  session/usage.py                       신규 ← usage.py (sessions_today 는 calls 에서)
  session/agents.py                      신규 ← agents/registry.py (상수 튜플 제거, AgentDef 기반)
  session/anonymize.py                   신규 ← agents/anonymize.py 그대로
  session/stages.py                      신규: check_rule(rule, stages) → 누락 사유 목록, CHECKPOINT_SCHEMA_VERSION 이사(run_store 가 re-export)
  session/runner.py                      신규 ← orchestrator/session.py (SessionKind 기반)
  execution/__init__.py, migrations.py   신규: EXECUTION = Migration(5, "execution", owner="kernel/execution")
  execution/types.py                     OrderIntent, ExecPolicy, OrderBookkeepingError, ORIGIN_CHECK, DEAD_STATUSES, APPROVAL_TTL, EXEC_KEY_STALE
  execution/idem.py                      IdemStore(Protocol), ExecKeyStore
  execution/ledger.py                    ← ledger/repo.py 의 orders·approvals·risk_checks·snapshot·집계 함수
  execution/core.py                      transmit, request_approval, approve, reject, expire_approvals, approval_fingerprint, ExecDeps
  execution/ports.py                     AccountsPort, InputsPort (Protocol)
  notify/messages.py                     + DecisionBrief, opinion_lines, order, order_unknown, bookkeeping_failure, execution_failure, approval_request, approve_hint, approval_rejected, approval_tampered, open_buys_cancelled, throttle_skip
src/trader/features/accounts/ports.py    신규: AccountsPortImpl + PORT; feature.py ports=(PORT,)
src/trader/features/ingest/ports.py      신규: InputsPortImpl + PORT; feature.py ports=(PORT,)
src/trader/features/spot/
  __init__.py, README.md, feature.py, api.py(빈 __all__)
  settings.py                            RiskLimits, HorizonProfile, RepriceCfg, DebateCfg; SECTIONS(settings.risk, settings.debate_rounds, settings.debate)
  schemas.py                             ← agents/schemas.py 의 현물 analyze 모델 + _STAGE_PATTERNS(10/20/30) + schema_for/json_schema(json_schema 는 kernel.session.agents 재사용)
  migrations.py                          MIGRATIONS = (Migration(6, "spot", …),)
  ledger.py                              ← repo.py 의 decisions/analyst_reports/debate_turns/ticker_names 함수 + ingest_checkpoint
  rules.py stance.py stops.py horizon.py costs.py tradability.py   ← risk/* (ticks 는 kernel.broker.ticks)
  summary.py                             ← orchestrator/summary.py
  tools.py                               ← orchestrator/tools.py 현물 부분 (ToolContext, status, checkpoint_*, bundle, summarize, trader_context, portfolio_context, requote, trade_block, submit_decision)
  tool_cli.py                            `tool` 하위 명령(status, collect, checkpoint {list,read,write,bundle,summarize}, submit-decision) + STAGE_RULES, TOOLS
  executor.py                            ← orchestrator/executor.py 현물 부분 (ExecContext, build_portfolio, execute, approve, reject, expire_approvals, cancel_open_buys, ORIGIN_TABLE)
  labels.py                              label(ticker, name), resolve(conn, market, ticker)   ← names.py 최소
  fake.py                                ← agents/fake.py 의 fake_report/fake_turn/fake_decision/run_fake_ticker/run_fake_analyze
  session_kinds.py                       ANALYZE = SessionKind(kind="analyze", …)
  agents/*.md                            8개
  cli.py                                 `trader spot analyze|approvals {list,approve,reject}|exec-key {list,release}`
  telegram.py                            /approve·/reject TelegramCommand
  jobs.py                                JobSpec approvals_expire(interval 60)
tests/kernel/{session,execution,lane}/…  tests/features/spot/…  tests/fixtures/agents/*.md(합성 2개)  tests/fixtures/stock_regression_v1.json
```

## 바꾸는 옛 단언 (사전 승인)

| 옛 테스트 | 바뀌는 것 | 이유 |
|---|---|---|
| 모든 이식 테스트 | `Settings()` → `settings` fixture, `RiskLimits()` → `spot.settings.RiskLimits()`, `AccountConfig(...)` → `make_account(...)`, `control.set_account_mode` → `accounts.modes.set_account_mode` (Ruling 17: 테스트는 `trader.features.accounts.modes` 를 직접 import — `accounts.api` 에 `set_account_mode`·`live_confirmed` 없음) | 설정 객체·계좌 모델 위치 |
| `test_executor.py` exec_key 계열 | `SELECT … FROM exec_keys` → `exec_locks`(+ 예약 열은 `buy_reservations`) | §2.4 테이블 분리 |
| `test_executor.py`·`test_tools.py` 결정 조회 | `decisions.round/updated_at/stop_floor` 참조 → `decision_rounds` 조인 | §2.4 |
| `test_session.py` 전부 | `run_session(spec, settings, conn, accounts, …)` → `run_session(spec, ANALYZE_KIND, ctx, …)`; fake 호출 확인은 합성 `SessionKind.fake` | SessionKind 레지스트리 |
| `test_session.py::test_frozen_session_allows_trader_tool_and_rewrites_prompt` | 프롬프트 재작성 확인은 `agents.orchestrator_prompt` 결과로 | 프롬프트 생성 위치 |
| `tests/agents/test_registry.py::test_common_rules_identical_prefix_across_all_agents` | md 16개 → `discover()` 로 모은 `Feature.agents` 전부(M4 는 8개) | 기능이 에이전트를 소유 |
| `tests/agents/test_schemas.py::test_schema_for_names` | `05_quant`·`35_calibration`·`_fo_*` 줄 삭제(M7·M8·M10) | 범위 |
| `test_cli_analyze.py`·`test_cli_approvals.py` | `["analyze", …]` → `["spot", "analyze", …]`, `["approvals", …]` → `["spot", "approvals", …]`; `quiet_project` 는 md 를 복사하지 않음(패키지 안) | 기능 sub-app 마운트 |
| `test_cli_analyze.py::test_status_shows_last_decisions` | 이식하지 않음(`trader status` 는 M12) | 범위 |
| `tests/notify/test_messages.py` 5건 | import 경로만 | — |
| `test_tools.py::test_submit_decision_runs_executor` 등 | 결과 dict 의 `memory_entry_id` 키 단언 삭제(M8) | 범위 |
| `tests/notify/test_messages.py::test_decision_brief_from_dict_and_model` | `agents.schemas.Decision` → 로컬 pydantic 모델(커널이 features 를 import 못 함, 단언 동일) | Ruling 8 |
| `test_executor.py::test_approve_legacy_null_fingerprint_passes` | → `test_approval_without_fingerprint_cannot_be_created`(`approvals.fingerprint NOT NULL` 이라 NULL 지문 승인은 `IntegrityError`) | Ruling 15 (v5 NOT NULL) |
| `test_executor.py::test_record_portfolio_false_skips_snapshot_and_killswitch` | → `test_record_portfolio_false_skips_snapshot`(killswitch 단언 제거, M6) | Ruling 15 |
| `tests/agents/test_registry.py` 공통 규칙 계약 | `common_rules` 끝 마침표 정규화(`.rstrip(".")`) 복원 — Task 3 단위 테스트 추가 | Ruling 18 (Ruling 5 폐기) |
| `test_tools.py::test_checkpoint_write_rejects_name_payload_mismatch` | 애널리스트 2개를 먼저 써 전제조건을 만족시킨 뒤 불일치를 검증 | Task 10 (e): 전제조건 실패만으로 통과하던 약한 단언 보강 |
| `test_tools.py` `bundle_trader` market_view 단언 | `in` → `not in`(market_view 컨텍스트 M6 이연) | Task 10 (b) 사전 승인 |
| `test_cli_analyze.py::test_cli_analyze_without_dry_run_executes_via_fake_broker` | FakeBroker 를 `AccountsPortImpl.brokers` 패치로 주입해 HOLD 경로 유지; `/status` 단언 `"상태" in m` | Task 12 |
| `test_stock_regression_fo.py` | 스냅샷 파일 `stock_regression_v1.json` 로 1회 재기준(원장 v1 열 이름) | 스펙 M4 행 |

---

### Task 1: 커널 계약·설정·부트 확장 — AgentDef·Port·lane·`tool` 루트

**Files:**
- Modify: `src/trader/kernel/registry/types.py`, `src/trader/kernel/registry/contracts.py`, `src/trader/kernel/registry/__init__.py`, `src/trader/kernel/config/__init__.py`, `src/trader/kernel/cli/boot.py`, `src/trader/kernel/cli/main.py`, `templates/config/settings.yaml`, `docs/architecture.md`(registry·config 절 한 줄씩)
- Create: `src/trader/kernel/lane/__init__.py`, `src/trader/kernel/lane/types.py`, `src/trader/kernel/cli/tool_cmd.py`, `src/trader/kernel/session/stages.py`
- Test: `tests/kernel/registry/test_contracts.py`(추가 5건), `tests/kernel/config/test_sections.py`(추가 2건), `tests/kernel/cli/test_boot.py`(신규 2건), `tests/kernel/cli/test_tool_root.py`(신규 3건), `tests/kernel/session/test_stages.py`(신규 3건), `tests/kernel/lane/test_types.py`(신규 1건)

**Interfaces:**
- Consumes: `registry.types.Feature`, `config.Section`, `cli.boot.Boot`.
- Produces:
  - `types.AgentDef(name: str, path: Path, schema: type | None = None)` frozen. `types.Port(name: str, build: Callable[[Any], Any])` — `build(boot) -> 구현체`.
  - `SessionKind` 필드 추가: `stdin_extra: Callable[[Any], dict] | None = None`(spec → stdin 추가 키). `StageRule(pattern: str, requires: tuple[str, ...] = (), min_counts: tuple[tuple[str, int], ...] = ())` — `requires` 는 정확한 단계 이름, `min_counts` 는 `(glob, n)`. `ToolCommand(name: str, handler: Any, stage_rules: tuple[StageRule, ...] = ())` — handler 가 `typer.Typer` 면 그룹, callable 이면 명령.
  - `Feature.agents: tuple[AgentDef, ...] = ()`, `Feature.ports: tuple[Port, ...] = ()`.
  - `kernel/lane/types.py`: `LANES = frozenset({"spot", "fo", "scalp"})`(contracts 는 여기서 import), `WorkItem(ticker: str, exchange: str | None)`, `LaneContext(settings, conn, state: Path, now: Callable[[], datetime], dry_run: bool, notifier, ports: Mapping[str, Any], ext: dict = {})` dataclass, `Lane(Protocol)` 스펙 §2.2 8 메서드 시그니처 그대로(구현은 레인).
  - `contracts.check` 추가 메시지: `에이전트 이름 중복: <name>`, `포트 중복: <name>`, `기능 <id>: tier=lane 이지만 LANES 에 없음` / `LANES 의 <id> 가 tier=lane 이 아님`(기능이 존재할 때만), `기능 <id>: origin <o> 는 orders.origin CHECK 밖`(`ORIGIN_CHECK` 는 Task 5 의 `execution.types` 에서 — Task 1 에서는 `contracts.ORIGIN_CHECK = ('llm','stop','hedge','scalp','rebalance','reprice','park','external','fo')` 상수로 두고 Task 5 가 execution.types 로 옮겨 re-export).
  - `config.LlmSettings`(옛 필드 10개·기본값, `timeout_for(kind) -> int`), `UsageThrottle`, `UsageProbe`; `KERNEL_SECTIONS` += `Section("settings","llm",LlmSettings,LlmSettings)`, `Section("settings","models",dict[str,str],dict)`, `Section("settings","claude_bin",str|None,lambda: None)`, `Section("settings","anonymize",bool,lambda: False)`.
  - `Boot.ports: dict[str, Any] = field(default_factory=dict)`; `boot(..., ports: bool = False)` 가 True 면 모든 `Feature.ports` 의 `build(boot_)` 결과를 이름으로 담는다(실패는 `ConfigError("포트 <name> 조립 실패: …")`). `ports=True` 는 `settings`·`secrets`·`conn` 을 함의한다.
  - `cli/tool_cmd.py`: `build_tool_app(features: list[Feature]) -> typer.Typer`(`trader tool <name>`; 이름 중복은 `RegistryError`), `emit(payload: dict) -> None`(stdout 에 JSON 1줄 `ensure_ascii=False`, `ok` 가 False 면 `typer.Exit(1)`), `run_tool(fn: Callable[[], dict]) -> None`(예외를 `{"ok": False, "error": f"{type(e).__name__}: {e}"}` 로). `main.build_app` 이 `tool` 을 마운트(`features` 가 비어도 빈 그룹). 루트 로깅은 `tool` 에서 stdout 핸들러를 달지 않는다.
  - `session/stages.py`: `CHECKPOINT_SCHEMA_VERSION = 1`(run_store 는 여기서 import 해 re-export), `check_rule(rule: StageRule, stage: str, stages: Iterable[str]) -> list[str]`(패턴 불일치 → `["단계 <stage> 는 이 도구가 쓸 수 없음"]`, 누락 `requires` → `"<name> 없음"`, `min_counts` 미달 → `"<glob> <n>개 필요(현재 k)"`), `first_rule(rules, stage) -> StageRule | None`.

- [ ] **Step 1: 실패 테스트 — types·contracts** — `test_contracts.py` 에 `test_duplicate_agent_names_reported`, `test_duplicate_port_names_reported`, `test_lane_tier_must_match_lanes_both_ways`, `test_origin_outside_check_reported`, `test_stage_rule_defaults`; `tests/kernel/lane/test_types.py::test_lane_context_fields_and_lanes`. RED.
- [ ] **Step 2: 구현** — types·lane·contracts(`LANES` 이동, `registry/__init__` 공개면에 `AgentDef`·`Port` 추가). GREEN.
- [ ] **Step 3: 설정 섹션** — `test_sections.py` 에 `test_kernel_llm_section_defaults_and_timeout_for`(`timeout_for("screen") == 300`, 모르는 kind → 900), `test_kernel_models_claude_bin_anonymize_sections`; `templates/config/settings.yaml` 에 옛 `llm:`·`models:`·`claude_bin`·`anonymize` 블록 추가(주석 포함). RED → 구현 → GREEN(`test_templates.py` 포함).
- [ ] **Step 4: Boot.ports + tool 루트** — `tests/kernel/cli/test_boot.py::test_boot_ports_builds_each_feature_port`(합성 Feature 2개를 `discover` monkeypatch)·`test_boot_port_failure_is_config_error`; `test_tool_root.py::test_tool_mounts_feature_tools_as_group_and_command`, `test_emit_writes_single_json_line_and_exit_1_on_not_ok`, `test_run_tool_wraps_exception`. RED → `boot.py`·`tool_cmd.py`·`main.py` → GREEN.
- [ ] **Step 5: stages** — `test_stages.py::test_check_rule_pattern_requires_min_counts`, `test_first_rule_picks_matching_pattern`, `test_run_store_reexports_schema_version`. RED → 구현 → GREEN.
- [ ] **Step 6: 전체** — `uv run pytest -q`(기존 917 + 신규), `uv run ruff check src tests`. `docs/architecture.md` registry·config 절에 AgentDef/Port/llm 섹션 한 줄씩.
- [ ] **Step 7: Commit** — `feat(kernel): M4 전제 — AgentDef·Port·lane·StageRule·tool 루트·llm 설정 섹션·Boot.ports`

---

### Task 2: kernel/session 원장 v4 + calls + usage

**Files:**
- Create: `src/trader/kernel/session/migrations.py`, `src/trader/kernel/session/calls.py`, `src/trader/kernel/session/usage.py`
- Modify: `src/trader/kernel/registry/contracts.py`(`all_migrations` 가 `[*KERNEL, *KERNEL_MODULES, *기능]` 을 합친다 — `KERNEL_MODULES = [SESSION]` 은 contracts 안에서 `trader.kernel.session.migrations` 를 import; `ledger.migrations.KERNEL` 은 v1 그대로 두어 `init_schema(c, KERNEL)` 를 쓰는 커널 테스트가 깨지지 않는다), `src/trader/kernel/session/__init__.py`(공개면 나열), `tests/kernel/session/conftest.py`(신규: `conn` fixture = `all_migrations(discover())`)
- Test: `tests/kernel/session/test_calls.py`(신규 6건), `tests/kernel/session/test_usage.py`(← `../trading-agent/tests/test_usage.py` 11건), `tests/kernel/ledger/test_migrations.py`(v4 존재 1건 추가)

**Interfaces:**
- Consumes: `ledger.db.now_iso`, `ledger.runs`, `ledger.control.get_flag`, `config.KST`, `config.LlmSettings`.
- Produces:
  - v4 `session`: `agent_calls(id, run_id REFERENCES runs, ticker, market, agent NOT NULL, model, session_id, cost_usd TEXT, duration_ms INTEGER, status CHECK IN ('running','ok','error'), error, num_turns INTEGER, created_at NOT NULL)` + `ix_agent_calls_created(created_at)`, `ix_agent_calls_agent_created(agent, created_at)`; `llm_rate_limits(id, observed_at, source, status, five_hour_util REAL, five_hour_resets_at, seven_day_util REAL, seven_day_resets_at, raw_json)` + `ix_llm_rate_limits_observed(observed_at)`.
  - `calls.py`: `FAKE_MODEL = "fake"`, `ORCHESTRATOR = "orchestrator"`; `sessions_today(conn, now, *, kind: str | None = None) -> int`(agent='orchestrator' AND model != 'fake' AND created_at ≥ KST 오늘 00:00 UTC 변환; kind 면 `JOIN runs r ON r.id = run_id AND r.kind = ?`); `calls_today(conn, agent, now) -> int`; `start_call(conn, run_id, *, agent, model, ticker, market, session_id, now, cap: int | None = None, extra_cap: tuple[str, int] | None = None) -> int | None`(BEGIN IMMEDIATE 안에서 cap 검사(model=="fake" 면 생략) → INSERT running → commit; 초과면 rollback 후 None); `finish_call(conn, call_id, *, status, session_id=None, cost=None, duration_ms=None, error=None, num_turns=None)`(옛 `_finish_call` UPDATE 그대로).
  - `usage.py`: 옛 `usage.py` 공개면 그대로(`WINDOWS`, `RESUME_KINDS`, `THROTTLE_FLAG`, `read_rate_limits`, `ledger_windows(conn, now, cap)`, `usage_file`, `record_rate_limit(conn, info, *, now, source="session")`, `latest_observation(conn, now, file_path)`, `throttle_enabled(conn, settings) -> (bool, 'control'|'yaml')`, `session_allowance(conn, settings, *, kind, now) -> (bool, str | None)`), `settings` 는 `.llm.usage_throttle`·`.llm.usage_file`·`.llm.daily_session_cap` 만 읽는다(`SimpleNamespace` 로도 동작). 감속 등급 판정은 `kind in RESUME_KINDS` 대신 **`throttle: Literal["hard","soft"]` 인자**를 받는 `allowance(conn, settings, *, throttle, label, now)` 를 추가하고 `session_allowance` 는 `throttle = "soft" if kind in RESUME_KINDS else "hard"` 로 위임한다(옛 테스트 보존 + SessionKind.throttle 연결).

- [ ] **Step 1: 마이그레이션 테스트** — `tests/kernel/ledger/test_migrations.py::test_all_migrations_include_session_v4`(`all_migrations(discover())` 에 version 4 `owner="kernel/session"` 이 있고 `init_schema` 뒤 `agent_calls`·`llm_rate_limits` 테이블 존재). RED → `session/migrations.py` + `contracts.all_migrations` → GREEN. 기능 테스트 conftest 는 `all_migrations(discover())` 라 자동 반영.
- [ ] **Step 2: calls 테스트** — `test_calls.py`: `test_sessions_today_counts_orchestrator_non_fake_since_kst_midnight`, `test_sessions_today_by_kind_joins_runs`, `test_start_call_inserts_running_and_respects_cap`, `test_start_call_cap_ignored_for_fake`, `test_start_call_extra_cap_counts_by_runs_kind`, `test_finish_call_updates_status_cost_turns`. RED → 구현(옛 `session._start_call/_finish_call`·`tools.sessions_today`·`quick.calls_today` 본문) → GREEN.
- [ ] **Step 3: usage 이식** — 옛 `tests/test_usage.py` 11건 → `tests/kernel/session/test_usage.py`(import 경로, `Settings()` → `SimpleNamespace(llm=LlmSettings())`, `tools.sessions_today` → `calls.sessions_today`). RED → `usage.py` → GREEN.
- [ ] **Step 4: 전체·커밋** — `uv run pytest -q`, ruff. `feat(kernel/session): 원장 v4 agent_calls·llm_rate_limits, calls(세션 행·상한), usage(사용량 감속) 이식`

---

### Task 3: kernel/session agents(md 로더·agents_json·orchestrator_prompt) + anonymize + 공통 규칙 계약 테스트

**Files:**
- Create: `src/trader/kernel/session/agents.py`, `src/trader/kernel/session/anonymize.py`, `tests/fixtures/agents/alpha.md`, `tests/fixtures/agents/orchestrator.md`(합성, 공통 규칙 6줄은 옛 문장 그대로), `tests/kernel/session/test_agents.py`(← `tests/agents/test_registry.py` 10건 중 8건 + 신규 3건), `tests/kernel/session/test_anonymize.py`(← 5건), `tests/kernel/registry/test_agents_contract.py`(신규 2건)
- Test: 위

**Interfaces:**
- Consumes: `registry.types.{AgentDef, SessionKind}`, `platform.bundle.tool_command`, `config.ConfigError`.
- Produces:
  - `agents.Agent(name, description, model, prompt)`; `split_frontmatter(text) -> (dict, str)`(YAML 오류 → ConfirError); `load_agent(defn: AgentDef) -> Agent`(파일 없음 → `ConfigError("에이전트 정의 없음: <name>")`, model 기본 "sonnet"); `model_for(name, models: Mapping[str, str], default) -> str`(`analyst-*` → "analyst", 그 외 `name.replace("-","_")`); `json_schema(model: type) -> str`(compact, `ensure_ascii=False`); `with_schema(prompt, schema: type | None) -> str`(`"type":"object"` 가 이미 있으면 그대로, 아니면 `"\n\n스키마: " + json_schema`); `agents_json(kind: SessionKind, agents: Mapping[str, AgentDef], models) -> dict[str, dict]`(`kind.agents` 순서, 각 `{description, prompt(with_schema), tools: [], model}`; 없는 이름 → ConfigError); `orchestrator_prompt(kind: SessionKind, agents: Mapping[str, AgentDef], *, debate_rounds: int, checkpoint_names: Sequence[str]) -> str`(`agents["orchestrator"]` 본문에서 `python -m trader tool` → `bundle.tool_command()`, `kind.prompt_section` 이 빈 문자열이면 모든 `## * 모드` 절을 지우고 아니면 그 절만 남김, `# 런타임 설정\n- debate_rounds=N` + 체크포인트 이름 줄 + `kind.schemas` 의 json_schema 들을 덧붙임); `MODE_HEADING = re.compile(r'^## \w+ 모드 \(`kind: "(\w+)"`\)', re.M)`; `common_rules(prompt) -> list[str]`(`# 공통 규칙` 절의 `^\d\. ` 줄에서 첫 `(` 앞까지 strip).
  - `anonymize.py`: 옛 61줄 그대로(`Alias`, `anonymize`, `deanonymize`).
  - 계약 테스트(`test_agents_contract.py`): `test_common_rules_prefix_identical_across_registered_agents`(`discover()` 의 모든 `Feature.agents` 를 로드해 `common_rules` 6줄이 모두 같고 길이 6; M4 에서 spot 이 등록되기 전까지는 등록이 없으면 skip 아닌 **pass**), `test_agent_schema_names_unique_and_paths_exist`.

- [ ] **Step 1: fixture md 2개 작성** — `alpha.md`(frontmatter `name: alpha`, `model: haiku`, 본문 `# 역할`, `# 출력 규칙`, `# 공통 규칙` 6줄), `orchestrator.md`(`# 역할`, `## resume 모드 (`kind: "resume"`)` 절 1개, `# 절차` 에 `python -m trader tool status` 문구, `# 공통 규칙`).
- [ ] **Step 2: 테스트 이식·신규** — 옛 `test_registry.py` 중 `test_all_agents_load_with_common_rules`·`test_role_specific_rules`·`test_market_agents_and_hedge_proposal_schema`·`test_scout_agent_and_discovered_schema` 는 spot 몫(Task 12 로 이연 표시); 나머지 6건(`test_agents_json_uses_settings_models`, `test_orchestrator_prompt_has_procedure`, `test_missing_agent_config_error`, `test_frontmatter_parse`, `test_agents_json_appends_schema_when_md_lacks_it`, `test_common_rules_identical_prefix_across_all_agents`→fixture 2개 기준)을 fixture 기반으로 + 신규 `test_prompt_section_keeps_only_requested_mode`, `test_prompt_rewrites_tool_command_when_frozen`(`bundle.is_frozen` monkeypatch → `trader tool`), `test_common_rules_extracts_six_prefixes`. 옛 `test_anonymize.py` 5건. RED.
- [ ] **Step 3: 구현** — `agents.py`·`anonymize.py`. GREEN.
- [ ] **Step 4: 계약 테스트** — `test_agents_contract.py` 2건(현재 등록 0 → pass). 커밋 `feat(kernel/session): agents md 로더·agents_json·orchestrator_prompt(SessionKind 기반)·anonymize 이식`

---

### Task 4: kernel/session runner — SessionSpec·run_session·build_command·run_claude·stream 파싱

**Files:**
- Create: `src/trader/kernel/session/runner.py`
- Modify: `src/trader/kernel/session/__init__.py`, `src/trader/kernel/platform/bundle.py`(`tool_bin_dir() -> Path` 추가: 번들은 `cli_executable().parent`, 개발은 `Path(sys.executable).parent`), `src/trader/kernel/platform/claude_bin.py`(신규: 옛 `platform/claude_bin.py` 의 `find_claude(settings_bin: str | None) -> Path`, `child_path(bin: Path) -> str`, `ClaudeNotFound(ConfigError)` 그대로)
- Test: `tests/kernel/session/test_runner.py`(← `tests/orchestrator/test_session.py` 33건 중 29건 — fake 4건은 Task 12), `tests/kernel/platform/test_claude_bin.py`(← 옛 `tests/platform/test_claude_bin.py` 전부, 있으면)

**Interfaces:**
- Consumes: Task 2 `calls`·`usage`, Task 3 `agents`, `registry.types.SessionKind`, `lane.types.LaneContext`, `ledger.runs`, `platform.procs.kill_group`, `platform.paths.project_root`.
- Produces:
  - `PipelineError(RuntimeError)`, `ThrottleBlocked(PipelineError)`, `SessionCancelled(subprocess.TimeoutExpired)`; 상수 `TERM_GRACE_S`, `KILL_WAIT_S`, `CANCEL_POLL_S`, `WIN_NEW_GROUP_NO_WINDOW`.
  - `SessionSpec(market: str, date: date, tickers: list[dict], dry_run: bool = True, asof: datetime | None = None, kind: str = "analyze", extra: dict = field(default_factory=dict))` — 옛 `candidates/context/entries/trigger/position` 은 `extra` 로(각 레인의 `SessionKind.stdin_extra(spec)` 가 꺼내 넣음). `SessionResult` 옛 필드 그대로.
  - `is_fake() -> bool`; `build_command(spec, kind, settings, bin_path, agents_file, *, session_id, prompt) -> list[str]`(Global Constraints 의 argv); `stdin_payload(spec, kind, asof) -> str`(기본 6키 + `kind.stdin_extra(spec)`); `parse_stream(stdout) -> (dict, dict | None)`; `run_claude(cmd, **kw) -> CompletedProcess`(옛 그대로; `cancel: threading.Event | None`); `terminate_group(proc)`.
  - `run_session(spec: SessionSpec, kind: SessionKind, ctx: LaneContext, *, agents: Mapping[str, AgentDef], runner=None, claude_bin=None, cancel=None, checkpoint_names: Sequence[str] = ()) -> SessionResult` — 순서: `ctx.settings.anonymize` → ConfigError; `calls.sessions_today ≥ llm.daily_session_cap` → PipelineError; fake 가 아니면 `usage.allowance(throttle=kind.throttle)` → ThrottleBlocked, `_guard_repeated_timeouts`(kind.kind=="analyze" & 티커 1개 — 옛 그대로, 하루 경계는 `execution.ledger.day_bounds` 가 아직 없으니 **runner 내부 `_day_bounds(d, market)`** 로 두고 Task 5 가 execution.ledger 로 옮겨 import); `extra_cap = (kind.runs_kind, getattr(llm, kind.extra_cap_key))` if `kind.extra_cap_key`; `runs.start(conn, kind.runs_kind, market)`; fake → `kind.fake(ctx, spec)` 가 돌려준 `list[str]` 를 `stages_written` 으로, `finish_call(model="fake", session_id="fake", cost=0.0)`; 실제 → cwd 생성·`agents.json` 쓰기(`agents_json`)·프롬프트(`orchestrator_prompt(kind, agents, debate_rounds=ctx.settings.debate_rounds if hasattr else 0, checkpoint_names)`)·env·runner 호출·`parse_stream`·`usage.record_rate_limit`·`finish_call`. 오류 문구 옛 그대로. 타임아웃 `ctx.settings.llm.timeout_for(kind.timeout_key)`.
  - `timeout_key` 규약: `SessionKind.timeout_key` 는 `llm.timeout_by_kind` 의 키(없으면 `timeout_s`).

- [ ] **Step 1: 테스트 이식** — 29건을 `test_runner.py` 로(치환표: `SessionSpec` 생성자, `run_session(spec, KIND, ctx, agents=AGENTS, runner=…)` 에서 `KIND = SessionKind(kind="analyze", agents=("alpha",), prompt_section="", schemas=(), timeout_key="analyze", runs_kind="analyze", extra_cap_key=None, throttle="hard", fake=lambda ctx, spec: ["30_decision"])`, `AGENTS` 는 Task 3 fixture 2개, `ctx = LaneContext(settings=SimpleNamespace(llm=LlmSettings(), models={}, anonymize=False, claude_bin=None, debate_rounds=1), conn=conn, state=project/"state", now=…, dry_run=True, notifier=None, ports={})`). 프로세스 그룹 테스트 2건은 `@pytest.mark.skipif(sys.platform == "win32")`. `test_throttle_raises_throttle_blocked_and_timeout_by_kind` 는 `timeout_key="resume"` 인 두 번째 KIND 로. RED.
- [ ] **Step 2: 구현** — `runner.py`(옛 session.py 본문, kind 분기 → `kind.*`), `bundle.tool_bin_dir`, `platform/claude_bin.py`. GREEN.
- [ ] **Step 3: 전체·커밋** — `feat(kernel/session): run_session(SessionKind 기반)·build_command·run_claude·stream 파싱 이식`

---

### Task 5: kernel/execution 원장 v5 + types + idem(ExecKeyStore) + ledger 원시

**Files:**
- Create: `src/trader/kernel/execution/__init__.py`, `migrations.py`, `types.py`, `idem.py`, `ledger.py`, `ports.py`
- Modify: `src/trader/kernel/registry/contracts.py`(`KERNEL_MODULES = [SESSION, EXECUTION]`; `ORIGIN_CHECK` 를 `execution.types` 에서 import), `tests/kernel/execution/conftest.py`(신규: `conn` = `all_migrations(discover())`), `src/trader/kernel/session/runner.py`(`_day_bounds` → `execution.ledger.day_bounds`)
- Test: `tests/kernel/execution/test_migrations.py`(2건), `test_types.py`(3건), `test_idem.py`(8건: 옛 `test_executor.py` 의 exec_key 규칙을 저장소 단위로 — `test_claim_is_exclusive`, `test_dead_order_key_is_reclaimed`, `test_stale_unsent_key_is_reclaimed_but_sent_key_is_not`, `test_closed_approval_key_is_reclaimed_pending_blocks`, `test_claim_reserved_runs_recheck_with_inflight_inside_write_lock`, `test_reservation_bound_order_visible_in_marks_is_excluded`, `test_release_and_has_approval`, `test_list_locks`), `test_ledger.py`(10건: insert_order/CHECK 위반, has_open_order_or_approval(include_filled), create/decide_approval 경쟁, expire_pending, insert_risk_check, snapshot_positions+account_nav, today_order_amount(exclude_filled)·pending_buy_amounts, held_since, trades_this_week(market), day_bounds)

**Interfaces:**
- Consumes: `ledger.db.now_iso`, `broker.models.{OrderRequest, Position}`, `config.{KST, MARKET_TZ}`.
- Produces:
  - v5 `execution`: `orders(id, decision_id INTEGER, account_id NOT NULL, ticker, market, side CHECK IN ('BUY','SELL'), qty INTEGER, order_type, price TEXT, status CHECK(…8), order_no, message, mode, created_at, amount TEXT, org_no, asset CHECK IN ('stock','fo') DEFAULT 'stock', origin CHECK(ORIGIN_CHECK) DEFAULT 'llm')` + `ix_orders_account_created(account_id, created_at)`, `ix_orders_decision(decision_id)`; `approvals(id, decision_id INTEGER NOT NULL DEFAULT 0, account_id, order_json, status CHECK IN ('pending','approved','rejected','expired','failed'), requested_at, expires_at, decided_at, fingerprint TEXT NOT NULL, stage, exec_key, origin, quote_price TEXT)`; `risk_checks(id, decision_id, account_id, approved INTEGER, adjusted_qty, reasons_json, created_at)`; `exec_locks(key PRIMARY KEY, created_at, order_id, approval_id, sent_at)`; `buy_reservations(key PRIMARY KEY REFERENCES exec_locks(key) ON DELETE CASCADE, account_id, market, ticker, side, amount TEXT, bound_at)`; `positions_snapshot(id, account_id, date, market, asset DEFAULT 'stock', ticker, qty, avg_price TEXT, current_price TEXT, eval_amount TEXT, created_at, UNIQUE(account_id, date, market, asset, ticker))`; `account_nav(id, account_id, date, market, nav TEXT, cash TEXT, created_at, UNIQUE(account_id, date, market))`.
  - `types.py`: `ORIGIN_CHECK`, `DEAD_STATUSES = ("rejected","cancelled","failed","expired")`, `dead_status_sql(col="status") -> str`, `APPROVAL_TTL`, `EXEC_KEY_STALE`, `OrderIntent(account_id, ticker, market, exchange, side, qty, order_type, price: Decimal | None, origin, risk_stage, order_stage, idem_key, decision_id: int | None, quote_price: Decimal | None = None, name: str | None = None, opinion: dict | None = None, asset: str = "stock")` frozen + `request() -> OrderRequest`; `ExecPolicy(mode: str, note: str | None, paused: bool, dry_run: bool)` frozen + `transmits` property(`mode != "off" and not paused and not dry_run`); `GatePlan(approved: bool, reasons: list[str], intents: list[OrderIntent], verdict_payload: dict)`(스펙 §2.2 — spot 은 계좌마다 intent 0~1개, fo 는 N 다리); `OrderBookkeepingError(RuntimeError)`(`order_no` 속성, 메시지 `주문 기록 실패 (접수번호 {order_no}): {cause}`).
  - `idem.py`: `IdemStore(Protocol)`: `claim(key, *, now) -> bool`, `claim_reserved(key, *, now, account_id, market, ticker, side, marks, recheck: Callable[[list[dict]], tuple[bool, Decimal | None]]) -> tuple[bool, list[dict]]`, `bind(key, *, order_id=None, approval_id=None, at=None)`, `mark_sent(key, *, sent_at)`, `release(key)`, `has_approval(key) -> bool`, `inflight(*, account_id, market, marks) -> list[dict]`, `list_all() -> list[dict]`. `ExecKeyStore(conn, *, stale=EXEC_KEY_STALE)` 구현 — 넘겨받기 규칙은 인벤토리 「Claim rule」 그대로(죽은 주문·닫힌 승인·만료 pending·10분 넘은 미발송만; `sent_at` 있고 `order_id` 없는 키는 절대 넘겨받지 않음). `claim_reserved` 는 BEGIN IMMEDIATE 안에서 `inflight` 를 읽고 `recheck(inflight)` 가 `(ok, amount)` 를 주면 잡으면서 `buy_reservations` 행을 쓴다.
  - `ledger.py`: 옛 repo 함수 시그니처 그대로(`insert_order`, `set_order_status`, `open_orders_today`, `has_open_order_or_approval`, `create_approval(conn, *, decision_id, account_id, order, requested_at, expires_at, fingerprint, stage=None, exec_key=None, origin=None, quote_price=None)`, `get_approval`, `pending_approvals`, `decide_approval`, `expire_pending(conn, now) -> list[int]`, `insert_risk_check`, `snapshot_positions(conn, *, account_id, d, nav, cash, positions, market, asset="stock")`(positions_snapshot + account_nav upsert), `latest_nav_before`, `nav_history`, `today_buy_tickers`, `today_order_amount`, `pending_buy_amounts`, `held_since`, `trades_this_week`, `realized_pnl_5d`(스냅샷·주문 기반 옛 공식 그대로), `ledger_marks`, `day_bounds(d, market) -> (iso_start, iso_end)`). 승인 `order_json` 사이드채널 키(`quote_price`·`order_stage`·`exec_key`·`origin`)는 **열로**(§2.4) — `get_approval` 은 `order` dict 에 다시 합쳐 돌려준다(옛 호출부 호환).
  - `ports.py`: `AccountsPort(Protocol)`: `accounts() -> list[Any]`, `brokers() -> dict[str, Any]`, `market_client() -> Any | None`, `policy(conn, account) -> tuple[str, str | None]`(account_mode — 최종 수정 웨이브에서 effective_mode 대신), `paused(conn) -> bool`, `holiday_fn() -> Callable[[str, date], bool] | None`; `InputsPort(Protocol)`: `collect(*, ticker, market, exchange, asof, force, conn_factory) -> dict`(`00_inputs` 저장 후 payload).

- [ ] **Step 1: 마이그레이션·types 테스트** — RED → 구현 → GREEN.
- [ ] **Step 2: idem 테스트 8건** — RED → `ExecKeyStore` → GREEN.
- [ ] **Step 3: ledger 테스트 10건** — RED → 구현 → GREEN. runner 의 `_day_bounds` 를 `execution.ledger.day_bounds` 로 교체(테스트 변동 없음).
- [ ] **Step 4: 전체·커밋** — `feat(kernel/execution): 원장 v5(orders·approvals·risk_checks·exec_locks·buy_reservations·positions_snapshot·account_nav)·OrderIntent·ExecPolicy·ExecKeyStore·원시 함수`

---

### Task 6: kernel/execution core — transmit·request_approval·approve·reject·expire·fingerprint + notify 문구

**Files:**
- Create: `src/trader/kernel/execution/core.py`
- Modify: `src/trader/kernel/notify/messages.py`(+ `DecisionBrief`, `opinion_lines`, `_order_line`, `_amount`, `order`, `order_unknown`, `bookkeeping_failure`, `execution_failure`, `approve_hint`, `approval_request`, `approval_rejected`, `approval_tampered`, `open_buys_cancelled`, `throttle_skip` — 옛 `notify/messages.py` 시그니처·문구 그대로), `src/trader/kernel/notify/__init__.py`(`__all__` 갱신)
- Test: `tests/kernel/notify/test_messages.py`(← 옛 `tests/notify/test_messages.py` 의 `test_order_with_opinion`, `test_order_failure_has_no_opinion_and_kind_tail`, `test_approval_request`, `test_decision_brief_from_dict_and_model`, `test_approval_request_shows_env_amount_and_kst_expiry` 5건), `tests/kernel/execution/test_core.py`(신규 12건: `test_transmit_accepted_inserts_order_binds_key_writes_checkpoint_and_notifies`, `test_transmit_rejected_releases_key_and_records_failed_row`, `test_transmit_unknown_keeps_key_and_notifies_unknown`, `test_transmit_bookkeeping_failure_is_critical_and_raises_for_accepted`, `test_transmit_bookkeeping_failure_for_rejected_is_logged_only`, `test_transmit_marks_sent_before_broker_call`, `test_request_approval_creates_row_with_fingerprint_and_binds_key`, `test_approve_executes_once_and_conditional_update_guards_race`, `test_approve_expired_or_missing_marks_expired`, `test_approve_paused_or_mode_changed_keeps_pending`, `test_approve_fingerprint_mismatch_fails_and_notifies`, `test_approve_recheck_reason_expires_and_reject_and_expire_all`)

**Interfaces:**
- Consumes: Task 5 전부, `broker.errors.{KisApiError, UNKNOWN_RESPONSE_CODE}`, `broker.models.OrderResult`, `session.run_store.{write_checkpoint, checkpoint_payload}`, `notify.messages`.
- Produces:
  - `approval_fingerprint(account_id: str, decision_id: int | None, order: dict) -> str`(옛 공식: `sha256(json.dumps({"account_id","decision_id": decision_id or 0,"order": order 에서 "name" 제외}, sort_keys=True, ensure_ascii=False, default=str))`).
  - `ExecDeps(conn, notifier, now: Callable[[], datetime], idem: IdemStore, broker_of: Callable[[str], Any | None], label: Callable[[str, str | None], str] = lambda ticker, name: ticker)` dataclass.
  - `transmit(deps, intent: OrderIntent, *, run_dir: Path, mode: str) -> dict` — 옛 `_place` 그대로(`mark_sent` → `broker.place_order(intent.request())` → status → `insert_order(... amount=qty*(price or quote_price), asset, origin)` → 기록 실패 처리 → `bind(order_id)` → 체크포인트 `{order_stage}_{account_id}` `{request, result, mode, status}`(OSError 삼킴) → `messages.order` 알림(unknown 은 `order_unknown`)). 반환 `{"status", "order_id", "order_no", "message", "request", "result"}`.
  - `request_approval(deps, intent, *, env: str, ttl=APPROVAL_TTL) -> int` — `create_approval(stage=intent.order_stage, exec_key=intent.idem_key, origin=intent.origin, quote_price, fingerprint=approval_fingerprint(account_id, decision_id, order_dict))`, `bind(approval_id)`, `messages.approval_request(...)` 전송. 실패 시 `release` 는 호출자.
  - `approve(deps, approval_id: int, *, policy_of: Callable[[str], ExecPolicy | None], run_dir_of: Callable[[dict], Path], decision_side_of: Callable[[int], str | None] | None = None, recheck: Callable[[dict, dict], str | None] | None = None, opinion_of: Callable[[dict], dict | None] | None = None) -> dict` — 옛 순서: take_pending(없음·pending 아님·만료 → `expired`) → `policy.paused` → `{"ok": False, "reason": "paused"}` pending 유지 → `policy is None` → 계좌 없음 `expired` → `policy.mode != "approve"` → pending 유지 `{"ok": False, "reason": "mode"}` → 지문 불일치(NULL 은 통과 — v1 은 NOT NULL 이라 항상 검사) → `failed` + `approval_tampered` → `decision_side_of(decision_id)` 가 있고 주문 side 와 다르면 `expired` → 브로커 없음 → pending → `recheck(approval, order)` 사유 → `expired` → `decide_approval(..., "approved")` 조건부 UPDATE(False → `{"ok": False, "reason": "race"}`) → `transmit(mode="approve")`; `OrderBookkeepingError` → `decide 'failed' expect='approved'` 후 재던짐; 비접수·non-unknown → `failed`. 반환 `{"ok", "approval_id", "status", "order"?}`.
  - `reject(deps, approval_id) -> dict`, `expire_approvals(deps) -> list[int]`.
  - `kernel.notify.__all__` 에 새 문구 함수 이름 추가. `DecisionBrief.of(decision: dict | BaseModel) -> DecisionBrief`.

- [ ] **Step 1: 문구 이식** — 옛 5건 → `tests/kernel/notify/test_messages.py`. RED → `messages.py` → GREEN.
- [ ] **Step 2: core 테스트 12건** — FakeBroker(`broker.fake`)·`ExecKeyStore`·`NullNotifier` 또는 `Sent` 리스트 notifier. RED → `core.py` → GREEN.
- [ ] **Step 3: 전체·커밋** — `feat(kernel/execution): executor 코어(transmit·승인 요청·approve/reject/expire·지문)·주문/승인 알림 문구 이식`

---

### Task 7: spot 골격 — 설정·스키마·원장 v6·결정 저장소 + accounts/ingest 포트

**Files:**
- Create: `src/trader/features/spot/{__init__,feature,api,settings,schemas,migrations,ledger,labels}.py`, `src/trader/features/spot/README.md`(골격: 책임·공개면·규약·「알려진 차이」 빈 절), `src/trader/features/accounts/ports.py`, `src/trader/features/ingest/ports.py`, `tests/features/spot/__init__.py`, `tests/features/spot/conftest.py`, `tests/features/spot/helpers.py`(← `tests/helpers_agents.py` 의 `ok_report/ok_turn/ok_decision` 그대로)
- Modify: `src/trader/features/accounts/feature.py`(`ports=(PORT,)`), `src/trader/features/ingest/feature.py`(`ports=(PORT,)`), `templates/config/settings.yaml`(`risk:`·`debate_rounds`·`debate:` 옛 블록)
- Test: `tests/features/spot/test_settings.py`(4건: 기본값, horizon 3종 필수 ValueError, 템플릿 로드, 섹션 키), `tests/features/spot/test_schemas.py`(← `tests/agents/test_schemas.py` 7건, 표의 수정), `tests/features/spot/test_ledger.py`(6건: ingest_checkpoint 가 analyst_reports/debate_turns/decisions+decision_rounds 를 멱등 적재, decision_id, get_decision(hydrate), set_skipped_debate, upsert_ticker_name/exchange, latest_decision), `tests/features/accounts/test_ports.py`(2건: policy=effective_mode·paused, brokers 가 키 없는 계좌 건너뜀), `tests/features/ingest/test_ports.py`(1건: collect 가 00_inputs 를 쓰고 payload 반환 — FakeMarket/FakeDart 주입)

**Interfaces:**
- Consumes: Task 1 `Port`·`AgentDef`, Task 5 `execution.ledger`, `accounts.api`(accounts 포트 안에서만), `ingest.api.{collect_and_save, build_input_deps}`(ingest 포트 안에서만), `kernel.session.agents.json_schema`, `kernel.session.stages.CHECKPOINT_SCHEMA_VERSION`.
- Produces:
  - `spot/settings.py`: `HorizonProfile`, `RepriceCfg`, `RiskLimits`(옛 13필드·검증·`_default_profiles`), `DebateCfg(skip_on_consensus: bool = True, consensus_min_conviction: float = 0.7)`; `SECTIONS = (Section("settings","risk",RiskLimits,RiskLimits), Section("settings","debate_rounds",int,lambda: 1)`(0..2 는 `Annotated[int, Field(ge=0, le=2)]`)`, Section("settings","debate",DebateCfg,DebateCfg))`.
  - `spot/schemas.py`: `Evidence, AnalystReport, Timing, StanceChange, DebateTurn, InvalidationRule, Decision, CallMeta`(옛 필드·검증 그대로), `ANALYST_ROLES = ("technical","fundamental","news_macro","sentiment")`, `_STAGE_PATTERNS`(10/20/30 세 줄), `schema_for(stage) -> type | None`, `json_schema = kernel.session.agents.json_schema`, `CHECKPOINT_NAMES = ("00_inputs","10_analyst_<role>","20_debate_r<n>_<side>","25_research_summary","30_decision","40_risk_check_<account>","50_order_<account>")`.
  - v6 `spot`: `decisions(id, date, market, ticker, action, target_weight TEXT, order_type, limit_price TEXT, stop_loss TEXT, take_profit TEXT, horizon_days, confidence REAL, rationale, data_json, source DEFAULT 'llm', skipped_debate INTEGER DEFAULT 0, created_at, UNIQUE(date, market, ticker))`, `decision_rounds(id, decision_id REFERENCES decisions, round INTEGER, action, stop_loss TEXT, source, skipped_debate INTEGER, data_json, created_at, UNIQUE(decision_id, round))`, `analyst_reports(id, decision_date, market, ticker, role, signal, score REAL, confidence REAL, data_json, created_at, UNIQUE(decision_date, market, ticker, role))`, `debate_turns(id, decision_date, market, ticker, round, side, stance, conviction REAL, data_json, created_at, UNIQUE(decision_date, market, ticker, round, side))`, `ticker_names(market, ticker, name, source, updated_at, PRIMARY KEY(market, ticker))`, `ticker_exchanges(market, ticker, exchange, updated_at, PRIMARY KEY(market, ticker))`.
  - `spot/ledger.py`: `ingest_checkpoint(conn, d, market, ticker, stage, cp) -> None`(10_/20_/30_ 정규식, 30_ 은 decisions upsert + round 0 행; `31_decision_r<n>` 은 M5 — 패턴만 두고 round n 행), `decision_id(conn, d, market, ticker) -> int | None`, `get_decision(conn, id, *, hydrate=False) -> dict | None`(hydrate 면 `data_json` 복원), `latest_decision(conn, market, ticker, *, before: date | None = None) -> dict | None`, `set_skipped_debate(conn, decision_id, flag)`, `decision_side(conn, decision_id) -> str | None`(action BUY/ADD → "BUY", SELL/TRIM → "SELL", 그 외 None), `upsert_ticker_name(conn, market, ticker, name, source)`, `upsert_ticker_exchange(conn, market, ticker, exchange)`, `exchange_of(conn, market, ticker) -> str | None`.
  - `spot/labels.py`: `label(ticker, name) -> str`(`'{name}({ticker})'` 이름 없으면 ticker), `resolve(conn, market, ticker) -> str | None`(ticker_names).
  - `accounts/ports.py`: `AccountsPortImpl(accounts: list[Account], secrets: Secrets)` — `accounts()`, `brokers()`(= `api.build_brokers(secrets, accounts)`, 키 없는 계좌는 경고 후 제외 — 옛 `_build_brokers` 규칙), `market_client()`(= `api.build_market` 시도, 실패 None), `policy(conn, account)` = `api.account_mode`(최종 수정 웨이브: 실전 파생 off 규칙 포함, 받은 계좌는 `split_accounts(...)[0]` 로 현물만), `paused(conn)` = `api.is_paused`, `holiday_fn()` = None(M5); `PORT = Port("accounts", lambda b: AccountsPortImpl(api.load_accounts(), b.secrets))`.
  - `ingest/ports.py`: `InputsPortImpl(secrets, cfg)` — `collect(*, ticker, market, exchange, asof, force, conn_factory)` = `build_input_deps(secrets, cfg, market=market, conn_factory=conn_factory)`(M3 이연 `InputDeps.market` 주입을 여기서 닫는다) + `collect_and_save`(`00_inputs` 저장, 있으면 `force` 아니면 그대로 읽어 반환); `build_input_deps`·`collect_and_save` 는 모듈 최상위에서 `from trader.features.ingest.api import …` 로 가져온다; `PORT = Port("inputs", lambda b: InputsPortImpl(b.secrets, load_sources(...)))`.
  - `spot/feature.py`: `FEATURE = Feature(id="spot", tier="lane", sections=SECTIONS, migrations=MIGRATIONS)`(cli·tools·agents·session_kinds·origins·jobs·telegram 은 뒤 Task 가 채움). `api.py` 는 `__all__ = []`.
  - `tests/features/spot/conftest.py`: `settings` fixture(`load("settings", build_registry(discover()), path=project/"config"/"settings.yaml")` — 파일 없음 → 기본값), `conn` fixture(all_migrations), `make_account`(accounts conftest 의 것 import), `accounts_port` fixture(`AccountsPortImpl(accounts, secrets=Secrets 빈 값)`), `D = date(2026, 10, 9)`, `T0 = datetime(2026, 10, 9, 1, 0, tzinfo=UTC)`, `PRICE = Decimal("71500")`.

- [ ] **Step 1: 설정·템플릿** — `test_settings.py` 4건 RED → `settings.py`·템플릿 → GREEN(`test_templates.py` 포함).
- [ ] **Step 2: 스키마** — 옛 7건 이식(표의 수정) RED → `schemas.py` → GREEN.
- [ ] **Step 3: 원장 v6·ledger·labels** — 6건 RED → 구현 → GREEN. `feature.py`·`README.md`·`api.py` 골격, `test_contracts.py`·`test_import_boundary.py` 통과 확인(`uv run pytest tests/kernel/registry -q`).
- [ ] **Step 4: 포트** — accounts 2건·ingest 1건 RED → `ports.py` 둘 + feature 등록 → GREEN. `tests/kernel/cli/test_boot.py::test_boot_ports_builds_each_feature_port` 는 합성이라 영향 없음; 실제 `boot(ports=True)` 가 scratch TRADER_HOME 에서 동작하는 테스트 1건 추가(`tests/features/spot/test_feature.py::test_boot_ports_has_accounts_and_inputs`).
- [ ] **Step 5: 전체·커밋** — `feat(spot): 골격 — risk/debate 설정·스키마·원장 v6(decisions+decision_rounds)·결정 저장소·accounts/ingest 포트`

---

### Task 8: spot 게이트 — rules·stance·stops·horizon·costs·tradability

**Files:**
- Create: `src/trader/features/spot/{rules,stance,stops,horizon,costs,tradability}.py`
- Test: `tests/features/spot/test_rules.py`(← `tests/risk/test_rules.py` 41건), `test_stops.py`(5), `test_horizon.py`(4), `test_tradability.py`(← `test_stock_lessons.py` 의 `test_block_reason`(parametrize)·`test_block_reason_accepts_quote_object`·`test_rules_killswitch_blocks_new_buys_only`·`test_rules_trade_block_rejects_buy_not_sell` 4건)

**Interfaces:**
- Consumes: `spot.schemas.Decision`, `spot.settings.{RiskLimits, HorizonProfile}`, `kernel.broker.models.Position`, `kernel.broker.ticks.quantize_price`.
- Produces: 옛 공개면·시그니처·상수·문구 그대로(인벤토리 `inv-risk.md`): `rules.Portfolio`(15필드·3메서드), `rules.RiskVerdict`, `rules.evaluate(decision, portfolio, *, limits, stance, market, price, today, is_stop_loss=False, holidays=None, regime="bull", is_hedge=False, hedge_max_weight=None, agent_tilt=0, is_scalp=False, change_pct=None, killswitch=None, trade_block=None) -> RiskVerdict`(게이트 순서·거부 문구 불변; `regime/is_hedge/agent_tilt/is_scalp/change_pct/killswitch/trade_block` 는 M5·M6·M9 호출자가 없어도 인자·동작 유지), `stance.{Stance, decide, limits_for, limits_for_regime, tilt_of}` + 상수 7, `stops.effective_stop`, `horizon.{name_of, profile_of, of_decision, trading_days_between, reanalyze_due, LABEL}`, `costs.{round_trip_bp, expected_value, expected_return}`, `tradability.{STATUS, WARN, block_reason}`.

- [ ] **Step 1: 테스트 이식** — 54건(import 경로: `trader.risk.X` → `trader.features.spot.X`, `trader.risk.ticks` → `trader.kernel.broker.ticks`, `helpers_agents` → `tests.features.spot.helpers`, `RiskLimits` → `spot.settings`). `test_horizon.py::test_profiles_must_be_complete` 는 `RiskLimits(horizon_profiles={...})` ValueError 그대로. RED.
- [ ] **Step 2: 구현** — 옛 6모듈 본문 그대로(import 만). GREEN.
- [ ] **Step 3: 커밋** — `feat(spot): 리스크 게이트 rules·stance·stops·horizon·costs·tradability 이식(54 테스트)`

---

### Task 9: spot summary — 수렴 감시·합의 토론 생략

**Files:**
- Create: `src/trader/features/spot/summary.py`
- Test: `tests/features/spot/test_summary.py`(← `tests/orchestrator/test_summary.py` 14건)

**Interfaces:**
- Consumes: `spot.schemas.{AnalystReport, DebateTurn}`.
- Produces: 옛 공개면 그대로 — `CONVERGENCE_GAP`, `CONSENSUS_ROLES`, `signal_direction`, `signed_conviction`, `conviction_gap`, `research_summary(round0, final, rounds, *, evidence_ids=None) -> dict`, `analyst_consensus(reports, min_conviction) -> dict | None`, `skipped_summary(consensus) -> dict`.

- [ ] **Step 1: 테스트 이식** — 14건(`test_bear_prompt_no_longer_forbids_bullish` 는 `spot/agents/researcher-bear.md` 를 읽으므로 Task 12 뒤에 GREEN — 이 Task 에서는 `pytest.importorskip` 아닌 **xfail(strict=False, reason="Task 12 md")** 로 두고 Task 12 가 xfail 표식을 지운다). RED.
- [ ] **Step 2: 구현 → GREEN → 커밋** — `feat(spot): summary(수렴 감시·합의 토론 생략) 이식`

---

### Task 10: spot tools — ToolContext·status·checkpoint {list,read,write,bundle,summarize}·StageRule 표·`trader tool`

**Files:**
- Create: `src/trader/features/spot/tools.py`, `src/trader/features/spot/tool_cli.py`
- Modify: `src/trader/features/spot/feature.py`(`tools=TOOLS`)
- Test: `tests/features/spot/test_tools.py`(← `tests/orchestrator/test_tools.py` 중 36건 — 인벤토리 「Core spot」 목록; `submit-*`·resume·requote 8건은 Task 11, ML/quant/calibration/memory 17건·theme/holding 2건은 이연), `tests/features/spot/test_tool_rules.py`(신규 3건: `test_stage_rules_cover_write_and_submit`, `test_submit_rule_requires_two_reports_and_summary`, `test_dry_run_env_missing_means_dry_run`(Review Focus 1))

**Interfaces:**
- Consumes: Task 1 `tool_cmd.{emit, run_tool}`, `stages.check_rule`, Task 7 `spot.{schemas, ledger, settings, labels}`, Task 9 `summary`, `kernel.session.run_store`, `kernel.session.calls.sessions_today`, `kernel.execution.ledger`(portfolio_context 의 스냅샷 읽기), `execution.ports.AccountsPort`.
- Produces:
  - `tools.ToolContext(settings, accounts: list, conn, state: Path, now, brokers: dict, notifier, dry_run: bool = True, holiday_fn=None, market_client=None, policy: Callable[[Any, Any], tuple[str, str | None]] | None = None, paused: Callable[[Any], bool] | None = None, inputs: Any = None)` dataclass(`inputs` 는 `InputsPort` 구현); `tool_ctx(boot: Boot) -> ToolContext`(`TRADER_DRY_RUN` 없음·"1" → True, "0" → False; `boot.ports["accounts"]` 로 accounts·brokers·market_client·policy·paused, `boot.ports["inputs"]` → `inputs`).
  - 옛 함수 시그니처 그대로: `ToolError(Exception)`, `status(ctx, market, d) -> dict`, `checkpoint_list(ctx, market, d, ticker)`, `checkpoint_read(ctx, market, d, ticker, stage, section=None)`, `checkpoint_write(ctx, market, d, ticker, stage, payload, *, agent, model=None) -> dict`(StageRule `WRITE_RULES` 로 허용 패턴·전제 검사 + 파일명↔페이로드 일치 + `round <= settings.debate_rounds` + `_unescape`), `checkpoint_bundle(ctx, market, d, ticker, *, kind, round=0, side=None) -> dict`(MIN_REPORTS·BUNDLE_LIMIT·`_shrink`), `checkpoint_summarize(ctx, market, d, ticker) -> dict`, `trader_context(ctx, market, d, ticker, inputs) -> dict`(quote·risk_limits·costs_bp·portfolio(`portfolio_context`)·holding; memory/calibration/quant/regime/theme 키는 **넣지 않음** — README 알려진 차이), `portfolio_context(ctx, market, d) -> dict`, `requote(ctx, market, ticker, exchange, inputs, now) -> tuple[Decimal | None, str | None, float | None]`(REQUOTE_AFTER_S), `trade_block(inputs) -> str | None`, `validate_ids(ticker, market)`, `_STAGE_RE`, `_TICKER_RE`.
  - `tool_cli.py`: `STAGE_RULES`: `WRITE_RULES = (StageRule(r"^10_analyst_(technical|fundamental|news_macro|sentiment)$", requires=("00_inputs",)), StageRule(r"^20_debate_r\d+_(bull|bear)$", requires=("00_inputs",), min_counts=(("10_analyst_*", 2),)))`, `SUBMIT_RULE = StageRule(r"^30_decision$", requires=("00_inputs", "25_research_summary"), min_counts=(("10_analyst_*", 2),))`; `tool_app = typer.Typer()` 에 `status`, `collect`(`--ticker [--market] [--exchange] [--asof] [--force]` → `ctx.inputs.collect(...)` → `{ok, path, errors}`; `inputs` 가 None 이면 `ToolError("수집 포트 없음")`), `checkpoint` 그룹(list/read/write/bundle/summarize). `submit-decision` 은 Task 11 이 더한다. `TOOLS = (ToolCommand("status", status_cmd, ()), ToolCommand("collect", collect_cmd, ()), ToolCommand("checkpoint", checkpoint_app, WRITE_RULES))`. 모든 명령은 `run_tool(lambda: …)` 로 감싸고 `emit`.
  - 옛 `tool` 명령 옵션 이름 그대로(`--ticker --market --date --stage --section --agent --json --model --kind --round --side --exchange --asof`).

- [ ] **Step 1: 테스트 이식 36건 + 신규 3건** — 치환표(`tools.ToolContext(...)` 생성자에 `policy=accounts_api.effective_mode, paused=accounts_api.is_paused` 추가; CLI 호출 `["tool", "checkpoint", "write", …]` 그대로). RED.
- [ ] **Step 2: 구현** — `tools.py`(옛 현물 부분, 지연 import 전부 제거), `tool_cli.py`. GREEN. `test_import_boundary` 통과.
- [ ] **Step 3: 커밋** — `feat(spot): tools(상태·체크포인트 읽기/쓰기/묶음/요약·StageRule 표)·trader tool 마운트 이식(39 테스트)`

---

### Task 11: spot executor + submit-decision + 승인 래퍼 + exec-key CLI

**Files:**
- Create: `src/trader/features/spot/executor.py`, `src/trader/features/spot/cli.py`(이 Task 는 `exec-key list|release` 만)
- Modify: `src/trader/features/spot/tools.py`(`submit_decision`), `src/trader/features/spot/tool_cli.py`(`submit-decision` 명령 + `TOOLS` 에 `ToolCommand("submit-decision", submit_cmd, (SUBMIT_RULE,))`), `src/trader/features/spot/feature.py`(`cli=spot_app`, `origins=ORIGINS`)
- Test: `tests/features/spot/test_executor.py`(← `tests/orchestrator/test_executor.py` 55건 — 인벤토리 그룹 1·2·5·6·7·8·9 중 `*` 아닌 것), `tests/features/spot/test_approval_fingerprint.py`(← 4건), `tests/features/spot/test_tools.py`(+ `test_submit_requires_reports_and_summary`, `test_submit_decision_runs_executor`, `test_submit_decision_records_us_exchange_from_inputs_meta`, `test_submit_rejects_invalid_decision`, `test_submit_without_ledger_connection_does_not_leave_decision_file`, `test_submit_rejects_stop_loss_at_or_above_price_for_any_action`, `test_submit_resumes_execution_when_decision_saved_but_not_executed`, `test_dry_run_risk_checks_do_not_block_live_resume`, `test_resume_runs_only_accounts_without_risk_check`, `test_resume_reruns_account_whose_order_never_went_out`, `test_submit_decision_requotes_when_inputs_are_stale`, `test_requote_returns_change_pct`, `test_fake_session_submit_does_not_record_portfolio` 13건), `tests/features/spot/test_exec_key_cli.py`(신규 2건: list, release 는 note ≥ 10자 필수 + control 플래그 `exec_key_released:<key>` — Review Focus 2)

**Interfaces:**
- Consumes: Task 5·6 `execution.{types, idem.ExecKeyStore, ledger, core}`, Task 7·8 `spot.{ledger, rules, stance, stops?, tradability, schemas, labels}`, `kernel.notify.messages`, `kernel.broker.{errors, models}`.
- Produces:
  - `executor.ORIGINS = (OriginSpec("llm","40_risk_check","50_order",True,frozenset(),"decision"), OriginSpec("stop","45_stop_loss","55_order_sl",True,frozenset({"min_hold","weekly_cap","decision_idem"}),"stage"), OriginSpec("hedge","46_hedge","56_order_hedge",True,frozenset({"min_hold","weekly_cap","regime"}),"decision"), OriginSpec("scalp","47_scalp","57_order_scalp",False,frozenset({"min_hold","weekly_cap","expected_return"}),"stage"), OriginSpec("rebalance","43_rebalance","53_order_rebalance",False,frozenset(),"stage"), OriginSpec("reprice","42_reprice","52_order_reprice",True,frozenset(),"decision"), OriginSpec("park","48_park","58_order_park",False,frozenset({"min_hold","weekly_cap","regime"}),"stage"))` — `Feature.origins` 로 등록(계약 검사 ORIGIN_CHECK ⊇).
  - `executor.ExecContext(settings, accounts: list, conn, brokers: dict, policy: Callable[[Any, Any], tuple[str, str | None]], paused: Callable[[Any], bool], notifier=NullNotifier(), state: Path | None = None, now=lambda: datetime.now(UTC), holiday_fn=None, record_portfolio: bool = True, market_client=None)`.
  - `executor.build_portfolio(conn, broker, account, market, today, *, now, record: bool) -> rules.Portfolio`(옛 본문에서 memory/killswitch/sector/hedge/theme 인자 제거 — `sector_of=None`, `hedge_tickers=frozenset()`, `theme_of=None`; `capital_cap` 적용 유지; `record` 면 `snapshot_positions`).
  - `executor.execute(ctx, decision, *, date, market, price, exchange=None, dry_run=True, is_stop_loss=False, decision_id=None, resume_round=None, is_hedge=False, skip_accounts=None, extra_note=None, scalp_leg=None, is_reprice=False, is_rebalance=False, change_pct=None, max_qty=None, trade_block=None, is_park=False, exec_tag=None, park_max_weight=None) -> list[dict]` — 옛 시그니처 그대로. 내부는 `origin_of(flags) -> OriginSpec`(우선순위 stop > scalp > reprice > rebalance > hedge > park > resume > llm)으로 `(risk_stage, order_stage, origin, has_decision_row)` 를 한 표에서 얻고, 계좌마다 `_execute_account` 가 ① `ctx.policy(conn, account)` + `ctx.paused(conn)` + `dry_run` → `ExecPolicy` ② `stance.decide(realized_pnl_5d)` ③ `build_portfolio`(실패 → 거부 verdict "잔고 조회 실패로 판정 불가") ④ `rules.evaluate(...)` ⑤ 결정 단위 멱등(`has_open_order_or_approval`, stop 제외) ⑥ `max_qty` ⑦ `ExecKeyStore.claim/claim_reserved`(BUY 는 재검사 `_with_reservations`) ⑧ verdict 보강(mode·note·stance·dry_run)·`40_…` 체크포인트·`insert_risk_check`(decision_id 있을 때) ⑨ `GatePlan(approved, reasons, intents=[OrderIntent] | [], verdict_payload)` 조립(`plan_of(verdict, origin, account, …) -> GatePlan` 공개 함수 — `Lane.plan` 이 돌려주는 값) → intent 가 있고 `policy.transmits` 면 `policy.mode == "approve" and not is_stop_loss` → `core.request_approval` else `core.transmit`. `OrderBookkeepingError` 수집·재던짐, 기타 예외 → `messages.execution_failure` + `{"error"}`. LLM 결정이 BUY/ADD 가 아니고 dry_run 아니면 `cancel_open_buys`. 결과 dict 키 옛 그대로(`account_id, mode, note, stance, approved, qty, reasons, dry_run, order, approval_id`).
  - `executor.cancel_open_buys(conn, broker, account_id, market, day, *, ticker, exchange_of, keep=None, side="BUY") -> tuple[int, int]`.
  - `executor.approve(ctx, approval_id) -> dict` = `core.approve(deps, id, policy_of=…(ExecPolicy from ctx.policy/paused), run_dir_of=…(결정일·시장·종목으로 `run_store.run_dir`), decision_side_of=spot.ledger.decision_side, recheck=_approval_recheck)` — `_approval_recheck(approval, order)` 는 BUY(hedge 제외)만 일일 손실 한도(`settings.risk.daily_loss_stop_pct`, 잔고 실패 → None 아닌 **pending 유지 사유 "balance"**)를 본다(killswitch·bear 재검사는 M6 — README). `reject(ctx, id)`, `expire_approvals(ctx) -> list[int]`.
  - `executor.deps_of(ctx) -> core.ExecDeps`(`idem=ExecKeyStore(ctx.conn)`, `broker_of=ctx.brokers.get`, `label=labels.label`).
  - `tools.submit_decision(ctx, market, d, ticker, payload, *, agent, meta=None, stage=None) -> dict` — 옛 순서(SUBMIT_RULE 검사 → Decision 검증 → ticker 일치 → 00_inputs quote 필수 → stop_loss ≥ price 거부 → 멱등/재개 → `30_decision` 저장(`checkpoint_payload(data, call)`) → `_ingest_run` → ticker name/exchange upsert → `set_skipped_debate` → `requote` → `execute(dry_run=ctx.dry_run, extra_note, change_pct, trade_block, skip_accounts)`), 반환 `{ok, path, stage, action, price, requote, dry_run, executions, resumed_execution?, already?}`.
  - `spot/cli.py`: `spot_app = typer.Typer(help="현물 레인")`, `exec-key list`(표: key, created_at, order_id, approval_id, sent_at), `exec-key release <key> --note TEXT`(note < 10자 → exit 1 "사유는 10자 이상"; `control.set_flag(f"exec_key_released:{key}", json{note, at})` 를 먼저 남긴 뒤 `ExecKeyStore.release` — 최종 수정 웨이브에서 순서를 바꿈).

- [ ] **Step 1: 테스트 이식** — executor 55 + 지문 4 + tools 13 + 신규 2. 치환표(`ExecContext(... policy=accounts_api.effective_mode, paused=accounts_api.is_paused)`; `control.set_account_mode(conn, acct, "auto", confirm="LIVE")` → `accounts_api.set_account_mode(conn, account, "auto", confirm="LIVE")`; `exec_keys` → `exec_locks`/`buy_reservations`; `repo.X` → `execution.ledger.X` 또는 `spot.ledger.X`). RED.
- [ ] **Step 2: 구현** — `executor.py`, `tools.submit_decision`, `tool_cli` submit, `cli.py` exec-key, feature 등록. GREEN(`test_contracts` origins 포함).
- [ ] **Step 3: 커밋** — `feat(spot): executor(ORIGIN 표·게이트·멱등 claim·승인/주문)·submit-decision·승인 래퍼·exec-key CLI 이식(74 테스트)`

---

### Task 12: spot analyze 세션 — agents md·FAKE·SessionKind·`trader spot analyze`·`tool collect`

**Files:**
- Create: `src/trader/features/spot/agents/{orchestrator,analyst-technical,analyst-fundamental,analyst-news-macro,analyst-sentiment,researcher-bull,researcher-bear,trader}.md`, `src/trader/features/spot/fake.py`, `src/trader/features/spot/session_kinds.py`, `src/trader/features/spot/analyze.py`(CLI 본문: 수집 → 세션 → 결정 출력)
- Modify: `src/trader/features/spot/cli.py`(`analyze` 명령), `src/trader/features/spot/feature.py`(`agents=AGENTS`, `session_kinds=(ANALYZE,)`), `tests/features/spot/test_summary.py`(xfail 제거)
- Test: `tests/features/spot/test_fake.py`(← `test_session.py` 의 `test_fake_session_writes_all_stages`, `test_fake_skips_debate_on_analyst_consensus`, `test_fake_skips_existing`, `test_fake_session_without_inputs_fails` 4건 + ← `tests/agents/test_registry.py` 의 `test_all_agents_load_with_common_rules`, `test_role_specific_rules` 2건), `tests/features/spot/test_cli_analyze.py`(← `tests/test_cli_analyze.py` 7건: `quiet_project` 는 `sources.yaml` 전부 비활성·`accounts.yaml` paper-main·`boot` 의 `ports["inputs"]` 를 FakeMarket/FakeDart 기반 `InputsPortImpl` 로 monkeypatch, md 복사 없음; + 신규 `test_cli_analyze_us_without_exchange_creates_no_run_dir`(Review Focus 5), `test_allowed_tools_matches_tool_command_and_prompt`(M0 계약: `build_command` 의 `--allowedTools` 값 == `f"Bash({bundle.tool_command()} *)"` 이고 `orchestrator_prompt(ANALYZE, …)` 에 `bundle.tool_command()` 가 포함되며 `python -m trader tool` 은 frozen 일 때 남지 않음)), `tests/kernel/registry/test_agents_contract.py`(이제 spot 8개로 실제 검사)

**Interfaces:**
- Consumes: Task 3·4 `kernel.session.{agents, runner}`, Task 10·11 `spot.tools`, Task 1 `AgentDef`·`SessionKind`, `lane.types.LaneContext`, `execution.ports.InputsPort`.
- Produces:
  - `spot/fake.py`: `FAKE_MODEL`, `fake_report(ticker, asof, role)`, `fake_turn(side, round)`, `fake_decision(ticker)`, `_call(agent)`, `run_fake_ticker(ctx: ToolContext, ticker, market, d, debate_rounds, trigger=None) -> list[str]`(옛 본문 — 10_×4 → summarize → 합의면 생략, 아니면 20_ r0..N → 재요약 → 30_decision HOLD; trigger 는 M5), `run_fake_analyze(lane_ctx: LaneContext, spec: SessionSpec) -> list[str]`(spec.tickers 마다 `run_fake_ticker`; `ToolContext` 는 `lane_ctx.ports["accounts"]` 로 조립, `dry_run=spec.dry_run`).
  - `spot/session_kinds.py`: `AGENT_NAMES = ("analyst-technical","analyst-fundamental","analyst-news-macro","analyst-sentiment","researcher-bull","researcher-bear","trader")`, `AGENTS = tuple(AgentDef(name, Path(__file__).parent/"agents"/f"{name}.md", schema) for …)` + `AgentDef("orchestrator", …, None)`(schema: analyst-* → AnalystReport, researcher-* → DebateTurn, trader → Decision), `ANALYZE = SessionKind(kind="analyze", agents=AGENT_NAMES, prompt_section="", schemas=(AnalystReport, DebateTurn, Decision), timeout_key="analyze", runs_kind="analyze", extra_cap_key=None, throttle="hard", fake=run_fake_analyze, stdin_extra=None)`.
  - `spot/analyze.py`: `run_analyze(boot: Boot, *, ticker, market, exchange, asof, dry_run, skip_collect) -> int`(옛 `cli.analyze` 본문: US 는 `--exchange` 필수(검사 전에 어떤 디렉터리도 만들지 않음), `day = run_date_for(market, asof or now)`, `00_inputs` 없으면 `ports["inputs"].collect`(`--skip-collect` 면 ConfigError), `LaneContext(settings, conn, state, now, dry_run, notifier=make_notifier(...), ports=boot.ports)`, `run_session(SessionSpec(...), ANALYZE, ctx, agents=by_name(FEATURE.agents), checkpoint_names=CHECKPOINT_NAMES)`, 실패 → exit 1, 성공 → `30_decision` 요약 출력).
  - `spot/cli.py analyze`: 옵션 `--ticker`(필수) `--market KR|US` `--exchange` `--asof ISO` `--dry-run` `--skip-collect`.
  - `ingest/ports.py` 는 `build_input_deps` 를 모듈 이름공간으로 import 해 두어 테스트가 `monkeypatch.setattr(ingest_ports, "build_input_deps", fake)` 로 바꿀 수 있게 한다(Task 7 에서 그렇게 만든다).

- [ ] **Step 1: md 복사** — 8개 복사(바이트 동일 확인 `cmp`; `orchestrator.md` 만 모드 절 7개 삭제). `python -c` 로 공통 규칙 6줄 prefix 가 8개 모두 같음을 출력해 확인.
- [ ] **Step 2: 테스트 이식·신규** — fake 4 + registry 2 + cli 7 + 신규 2. `quiet_project` 는 `boot` 를 monkeypatch 하지 않고 **`features.ingest.ports.InputsPortImpl` 의 `deps` 조립 함수**를 FakeMarket/FakeDart 로 바꾼다(ingest 테스트의 `helpers`/`FakeMarket` 재사용). RED.
- [ ] **Step 3: 구현** — `fake.py`·`session_kinds.py`·`analyze.py`·CLI·`tool collect`·feature 등록. GREEN. `test_agents_contract.py` 가 8개로 통과, `test_summary.py` xfail 제거.
- [ ] **Step 4: 실기 확인(scratch)** — `TRADER_HOME=<tmp> uv run trader init` 뒤 `sources.yaml` 소스 전부 off·`accounts.yaml` 자리표시자 계좌(paper, mode auto)로 두고 `FAKE_CLAUDE=1 TRADER_HOME=<tmp> uv run trader spot analyze --ticker 005930 --dry-run` 실행 → `state/runs/<오늘>/KR/005930/` 에 Goal 의 체크포인트 집합(13개 파일, debate_rounds=1) 확인. 결과 파일 목록을 report 에 적는다(수집은 KIS 키 없이 절 오류로 남고 `00_inputs` 는 생성돼야 함 — 그렇지 않으면 ingest 포트가 키 없음을 `ConfigError` 로 올리는지 확인하고 Ruling).
- [ ] **Step 5: 커밋** — `feat(spot): analyze 세션 — 에이전트 md 8·FAKE·SessionKind analyze·trader spot analyze·tool collect(15 테스트)`

---

### Task 13: 승인 경로 — `trader spot approvals`·텔레그램 `/approve`·`/reject`·만료 잡

**Files:**
- Create: `src/trader/features/spot/telegram.py`, `src/trader/features/spot/jobs.py`
- Modify: `src/trader/features/spot/cli.py`(`approvals list|approve|reject`), `src/trader/features/spot/feature.py`(`telegram_commands`, `jobs=(APPROVALS_EXPIRE,)`), `src/trader/kernel/notify/commands.py`(`approval_line(approval: dict) -> str` 헬퍼가 옛 `notify/commands.approval_line` 이면 여기로; 없으면 spot `messages.py`)
- Test: `tests/features/spot/test_cli_approvals.py`(← `tests/test_cli_approvals.py` 3건: `test_cli_approvals_flow`, `test_cli_telegram_poll_processes_commands`(`trader telegram poll` 이 `/approve <id>` 를 spot 명령으로 처리), `test_cli_telegram_poll_without_config_fails`), `tests/features/spot/test_telegram.py`(신규 3건: 실전 계좌 승인은 `/approve <id> 실전` 확인어 필요, 신뢰 밖 발신자 거부(`untrusted_ok=False`), paused/모드 변경 시 pending 유지 — Review Focus 4), `tests/features/spot/test_jobs.py`(신규 1건: `approvals_expire` 잡이 만료분을 expired 로)

**Interfaces:**
- Consumes: Task 11 `executor.{approve, reject, expire_approvals, ExecContext}`, `kernel.notify.commands.CommandContext`, `registry.types.{TelegramCommand, JobSpec}`, `execution.ledger.pending_approvals`.
- Produces:
  - `spot/cli.py`: `approvals list`(만료 처리 뒤 pending 목록 한 줄씩 `approval_line`), `approvals approve <id>`, `approvals reject <id>` — `ExecContext` 는 `boot(ports=True)` 의 `ports["accounts"]` 로 조립(`exec_ctx(boot) -> ExecContext`).
  - `spot/telegram.py`: `TELEGRAM_COMMANDS = (TelegramCommand("approve", False, "/approve <id> [실전] — 승인 요청 실행", handle_approve), TelegramCommand("reject", False, "/reject <id> — 승인 요청 거부", handle_reject))`; 핸들러 `(ctx: CommandContext, arg: str | None) -> str`, 실전 계좌(approval 의 account env == "real")는 arg 에 `실전` 이 없으면 "실전 계좌 승인은 `/approve <id> 실전` 으로 확인하세요" 반환.
  - `spot/jobs.py`: `APPROVALS_EXPIRE = JobSpec(id="approvals_expire", kind="approvals", trigger="interval", per_market=False, schedule_key=None, interval_s=60, enabled=None, gate="none", heavy=False, misfire="idempotent", catch_up="none", start_soon_s=30, body=expire_body)` — `expire_body(ctx: DaemonDeps, job) -> dict` 가 `exec_ctx` 조립 후 `expire_approvals` → `{"expired": [...]}`.

- [ ] **Step 1: 테스트 이식·신규** — RED. **Step 2: 구현 → GREEN.** **Step 3: 커밋** — `feat(spot): 승인 CLI·텔레그램 /approve·/reject(실전 확인어)·approvals_expire 잡(7 테스트)`

---

### Task 14: 골든 회귀 재기준 + analyze 체크포인트 집합 테스트

**Files:**
- Create: `tests/features/spot/regression_scenario.py`(← `tests/stock_regression_scenario.py`, 새 import·`ExecContext`·`make_account`), `tests/features/spot/test_regression.py`(← `test_stock_regression_fo.py` 2건 + 신규 `test_fake_analyze_writes_expected_checkpoint_set`), `tests/fixtures/stock_regression_v1.json`(생성)
- Modify: `docs/superpowers/plans/…`(없음), `tests/features/spot/conftest.py`(필요 시 `update_golden` 헬퍼)

**Interfaces:**
- Produces: `regression_scenario.run(settings, conn, accounts_port) -> dict`(옛 구조 `buy/hold/orders/risk_checks` 그대로, VOLATILE 키 제거); `test_stock_paths_unchanged` 는 `TRADER_UPDATE_GOLDEN=1` 이면 스냅샷을 다시 쓰고 pass, 아니면 `==` 비교; `test_stock_orders_tagged_stock_and_accounts_default`; `test_fake_analyze_writes_expected_checkpoint_set`(`run_fake_analyze` 로 `sorted(list_checkpoints(run_dir)) == EXPECTED`(13개, debate_rounds=1, 계좌 paper-main·real-main)).

- [ ] **Step 1: 시나리오·테스트 이식** — RED(스냅샷 없음). **Step 2: `TRADER_UPDATE_GOLDEN=1 uv run pytest tests/features/spot/test_regression.py -q`** 로 1회 생성 → 생성 JSON 을 열어 옛 골든과 **값 비교**(qty 14, LIMIT 71000, paper-main accepted FAKE-1, real-main off, hold ["HOLD: 주문 없음"], orders 1행, risk_checks 4; 다른 값은 열 이름·`decision_rounds` 유래만 허용 — 차이를 report 에 표로) → env 없이 재실행 GREEN. **Step 3: 커밋** — `test(spot): 골든 회귀 v1 재기준·FAKE analyze 체크포인트 집합 테스트`

---

### Task 15: 문서·체크리스트·공개면 점검

**Files:**
- Modify: `src/trader/features/spot/README.md`(책임·공개면·규약·「알려진 차이」: `_STAGE_KIND` 51/55 만, `trader analyze` → `trader spot analyze`·`approvals` → `spot approvals`, memory/calibration/quant/regime/theme 컨텍스트 미주입(M6~M8), 승인 재검사 killswitch·bear 미구현(M6), `ask_json`·`probe_usage` 미이식(M8), resume/31_ 은 M5, `agents_dir()` 미사용, 지연 import 전부 포트·커널 경유), `docs/architecture.md`(session·execution·lane 절 신설, registry·cli 절 갱신), `CLAUDE.md`(실행 명령 표: `trader spot analyze …`(FAKE 규칙), `trader spot approvals list|approve|reject`, `trader spot exec-key list|release`, `trader tool …`(세션 전용); 불변식의 "(M4 에서 구현)" 표기 제거; ≤ 16KB 확인 `wc -c`), `docs/TODO.md`(M3 `PR #4 머지` 체크, M4 Task 15개 체크 + 이연 목록: M5 resume·31_·trigger·stop·monitor·`telegram_poll`/outbox 잡·holiday_fn·기동 시 `runs`/`agent_calls` 의 stale `running` 행 정리·게이트 공급 경로(`Feature.gates`)·시그널 `wait=True` 테스트·`accounts_io.blockers` 실제 스키마(orders·approvals·positions_snapshot); M6 killswitch·blackout·hedge·park·regime 컨텍스트·승인 bear/killswitch 재검사·`test_rules` 의 regime 호출자; M7 quant·RL·theme; M8 memory·calibration·quick.py(`ask_json`·`probe_usage`)·`test_quick` 9; M10 fo 세션·`test_session_fo`·`test_schemas_fo`·`test_ticks_fo`·`test_usage_fo`; M11 `decision_trace`; M12 `trader status`·`test_status_shows_last_decisions`·`paths.agents_dir` 정리·native-host argv 우회(번들)·`test_native_origin_routes_before_cli`; Ruling: `trader collect`·`trader analyze` 최상위 별칭은 두지 않는다(기능 sub-app 일관)), `src/trader/kernel/session/__init__.py`·`execution/__init__.py`(공개면 `__all__`)
- Test: `uv run pytest -q`, `uv run ruff check src tests`, `wc -c CLAUDE.md` ≤ 16384, `uv run trader dev gen-sse-keys --check`(변동 없음)

- [ ] **Step 1: README·architecture·CLAUDE.md·TODO** 작성. **Step 2: 전체 검증** 출력 기록. **Step 3: 커밋** — `docs: M4 spot README·architecture(session/execution/lane)·CLAUDE.md 명령 표·TODO 체크리스트`

---

## 이식 테스트 집계(PR 본문용)

| 옛 파일 | 건수 | 새 위치 | 이연 |
|---|---|---|---|
| tests/test_usage.py | 11 | tests/kernel/session/test_usage.py | test_usage_fo 1 → M10 |
| tests/agents/test_registry.py | 10 | kernel/session/test_agents.py 6 + features/spot/test_fake.py 2 | market/scout 2 → M6/M7 |
| tests/agents/test_schemas.py · test_anonymize.py | 7 · 5 | features/spot/test_schemas.py · kernel/session/test_anonymize.py | test_schemas_fo 4 → M10 |
| tests/orchestrator/test_session.py | 33 | kernel/session/test_runner.py 29 + spot/test_fake.py 4 | — |
| tests/orchestrator/test_summary.py | 14 | features/spot/test_summary.py | — |
| tests/orchestrator/test_tools.py | 62 | features/spot/test_tools.py 49 | ML/quant/calibration/memory 17 → M7/M8, theme/holding 2 → M6, chasing theme 1 → M6 |
| tests/orchestrator/test_executor.py | 76 | features/spot/test_executor.py 55 | breaker 4 → M8, RL 8 → M7, stop/origin 5 → M5, tilt 1·killswitch 3 → M6 |
| tests/orchestrator/test_approval_fingerprint.py | 4 | features/spot/test_approval_fingerprint.py | — |
| tests/risk/test_rules.py · test_stops.py · test_horizon.py | 41 · 5 · 4 | features/spot/… | test_ticks_fo 2 → M10 |
| tests/risk/test_stock_lessons.py | 15 | features/spot/test_tradability.py 4 | 11 → M6(killswitch·capital_cap·health·CLI) |
| tests/notify/test_messages.py | 11 | kernel/notify/test_messages.py 5 추가 | 6 → 각 소유 M |
| tests/test_cli_analyze.py · test_cli_approvals.py | 8 · 3 | features/spot/test_cli_analyze.py 7 · test_cli_approvals.py 3 | status 1 → M12 |
| tests/test_stock_regression_fo.py | 2 | features/spot/test_regression.py | 재기준 |
| tests/orchestrator/test_quick.py | 9 | — | M8 |
| tests/orchestrator/test_run_store.py | 4 | (M3 완료) | — |
