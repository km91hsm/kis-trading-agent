# kis-trading-agent 리플랫폼 설계 (2026-11-02)

> 출처: 브레인스토밍(2026-11-02) + 옛 저장소 `trading-agent` 조사 3건 + 빌더 검토. 코드가 생기면 코드가 출처이고, 이 문서는 "왜" 와 경계만 남긴다.

## Context

`trading-agent`(GitHub 비공개, Phase 0~26, `src/trader` 약 58k 줄 · 웹 13k 줄 · pytest 236 파일 ≈ 2,300 함수 · vitest 39 파일)는
기능은 갖췄지만 구조가 한계에 닿았다. 조사로 확인한 사실:

- **기능 하나를 더하려면 12개 이상 파일의 약 25곳**을 손댄다 — `daemon.build_jobs` 표 + `job_body` if 체인 + 7개 kind 집합(`IDEMPOTENT/CATCH_UP/TRADING/RESTART_REQUEUE_KINDS` 등), `jobs.py` 7곳, `cli.add_typer`, `Settings`·`ScheduleConfig` 필드, 라우터 튜플, SSE `snapshot()` dict(+프런트 `NOISY_KEYS`·`nav.ts`), 텔레그램 정규식·help·`UNTRUSTED_OK`, `health` 영역 순서, `build_status`, `executor.execute` 의 `is_*` 플래그를 3곳(단계명 if 체인·결정행 규칙·`_origin_of`)에 같은 순서로.
- **현물·파생·단타는 "같은 모양" 이 아니다.** 현물+단타는 `executor.execute`·`risk/rules.evaluate` 를 공유하지만 fo 는 executor·게이트·멱등 장치(leg 행)·포지션 상태기계가 전부 따로 있고, 세 레인이 공유하는 것은 모드·일시정지·킬스위치·`orders`·`triggers` 뿐이다. 계좌 루프·기록 전용 판정·전송 뒤 unknown 기록·재개 로직·컨텍스트 dataclass 5종이 변형 복제돼 있다.
- **원장은 34번 덧댔다.** 후속 ALTER 로 붙은 열 20여 개, `control` 키-값에 사실상 테이블 15가족(계좌 모드·국면·킬스위치·운영 점검·`*_done:<날짜>` 마커·fo 플래그), 금액은 TEXT(Decimal)·REAL 혼재, `exec_keys` 는 잠금과 매수 예약을 겸함.
- **웹 API 는 화면 지향** — `/system/*` 11개 잡탕, `/control/*` RPC 식 POST, 계좌는 쿼리 인자, 대시보드 한 화면이 17개 호출.
- 규칙은 82KB `CLAUDE.md` + 586줄 `architecture.md` 산문에 있어 세션마다 컨텍스트를 크게 먹고 누락이 생긴다.

사용자는 이를 **새 공개 저장소 `km91hsm/kis-trading-agent`** 로 옮겨 심어 차세대 프로젝트로 리플랫폼한다. 그 전에 Codex 로 다시 만든 세이브티커 수집기(`codex/saveticker-extension`)를 옛 저장소 main 에 반영한다. 참고 자료(Notion: 트레이딩뷰 MCP 잭슨·모닝 브리핑·tradermonty 스킬)는 ④ 단계에서 쓴다.

## 사용자 결정 (브레인스토밍 확정)

| 항목 | 결정 |
|---|---|
| 성격 | 파이썬 데몬·게이트·executor·원장 구조 **유지**. 그 위에 (a) 소스 구조 재설계 (b) 웹 UI 재설계 (c) 트레이딩뷰 MCP·브리핑·복기 보강 |
| 저장소 | **새 저장소** `km91hsm/kis-trading-agent`, 로컬 `/Users/ohhyeonseok/LLM/kis-trading-agent`, **public**, GitHub Actions CI |
| 이식 범위 | **전량**(fo·scalp·RL 포함), 기능 동등 |
| 데이터 | 모의 운영 **0일부터** → 원장 **새 스키마 v1**, 옛 데이터 이전 코드 없음. 옛 `state/` 는 참고용 보존 |
| 스택 | 그대로: Python 3.12 + uv + pydantic + FastAPI/uvicorn + SQLite(WAL) + APScheduler / React + Vite + TS / `claude -p` + `.claude/agents/*.md` / PyInstaller |
| 재설계 목표 | ① 기능 단위 수직 분할 + 중앙 레지스트리 ② 현물·파생·단타 공통 Lane ③ 규칙을 산문→코드, CLAUDE.md 1/5 이하 ④ 원장·웹 API 자원 모델 |
| 이식 방법 | **A. 뼈대 먼저, 기능 순차 이식** — 각 기능은 옛 테스트를 새 구조로 옮겨 통과해야 완료 |
| 전환 | 옛 데몬은 새 저장소가 (1) 이식 테스트 전부 통과 (2) `FAKE_CLAUDE=1 --dry-run` 하루 무사고 를 넘길 때까지 운영, 그 뒤 `install` 교체 |
| 공개 위생 | `config/`(실제 계좌번호)·`secrets/`·`state/` 는 `.gitignore`, `templates/` 자리표시자만 커밋 |

## 독립 프로젝트 4개와 순서

1. **세이브티커 git 반영** (옛 저장소, 설계 불필요) — §1
2. **새 저장소 + 구조 재설계 + 전량 이식** — §2(설계)·§3(이식 순서)
3. **웹 UI 재설계** — §2.5 의 자원 API 위에서 별도 브레인스토밍 (ui-ux-pro-max → impeccable / design-taste-frontend). 시점은 M11 직전(아래 §3 참고)
4. **트레이딩뷰 MCP · 브리핑 · 복기 보강** — 별도 브레인스토밍. 기존 `briefing`·`reflect` 잡 확장, MCP 미설치 상태

---

## §2. 설계 — kis-trading-agent

### 2.0 의도·목표·비목표

- **의도**: 같은 기능을 "기능 하나 = 폴더 하나" 로 다시 앉혀, 기능 추가가 레지스트리 등록 한 곳으로 끝나고, 세 레인이 한 executor 코어·한 세션 러너·한 데몬 잡 모양을 쓰며, 규칙이 타입·전제조건 표·테스트로 강제되게 한다.
- **목표**: 위 4개. 완료 척도 = 옛 테스트 전부 이식·통과 + 레지스트리 계약 테스트 + CLAUDE.md ≤ 16KB.
- **비목표**: 매매 로직·리스크 수치·에이전트 프롬프트의 **의미 변경 없음**(이식은 동작 보존, 개선은 별도 Phase). 웹 UI 시각 재설계(③), MCP(④)는 이 설계 밖.
- **안티 목표(이렇게 되면 실패)**: 옛 데몬이 멈춘 채 새 데몬이 미완성인 기간이 생김 / 공개 저장소에 계좌번호·키·수집 원문이 올라감 / "parity" 라며 옛 if 체인을 폴더만 바꿔 그대로 옮김.
- **제약**: `.claude/settings.json` 거부 목록(실주문·데몬·install 은 사용자 실행), 셸 스크립트 금지(전부 파이썬), Windows 동일 실행, 공개 저장소 fixture 는 합성 데이터만.

### 2.1 목표 패키지 구조

`src/trader/` 를 **`kernel/`(기능을 모름) + `features/`(기능, 서로 직접 import 금지 — 커널 계약·레지스트리·이벤트로만)** 두 층으로 나눈다. 패키지·CLI 이름은 `trader` 유지(번들·문서 호환).

| 새 위치 | 들어갈 것 | 옛 출처 |
|---|---|---|
| `kernel/config` | 설정 로더 + **섹션 레지스트리**(기능이 자기 pydantic 섹션을 등록, `extra="forbid"` 유지), `templates/` 패리티 검사, `KST`·market_hours | `config.py`, `market_hours.py` |
| `kernel/ledger` | SQLite 연결·WAL·**마이그레이션 레지스트리**(기능별 파일, 전역 버전 순서, 추가만), 공용 repo 원시(주문·체결·포지션·스냅샷·control), 자원 뷰 | `ledger/db.py`·`repo.py`·`views.py` 공통부 |
| `kernel/broker` | KIS auth/client/market/broker/ws/fake, `tr_ids`, 교차 프로세스 레이트리미터, `_parse` | `broker/` 전부 |
| `kernel/session` | `claude -p` 러너, **SessionKind 레지스트리**(agents·프롬프트 절·스키마·stdin 추가분·timeout 키·runs.kind·추가 cap·감속 등급·FAKE 함수), agents md 로더, 사용량 감속 | `orchestrator/session.py`·`quick.py`, `agents/`, `usage.py` |
| `kernel/execution` | `OrderIntent`·`GatePlan`·`ExecPolicy`·`IdemStore` 프로토콜, **executor 코어**(계좌 루프·정책·게이트 체크포인트·멱등 claim·승인/주문·unknown 기록·orders 행·알림·`OrderBookkeepingError`), 승인(30분·지문·1회) | `orchestrator/executor.py` 1012~1079 + `fo/executor._transmit` 공통부 |
| `kernel/lane` | `Lane` 프로토콜, `LaneContext`, `JobSpec`, 파이프라인 훅 포인트 | 신규 |
| `kernel/registry` | `Feature` 계약(§2.3)과 `discover()` — `features/*` 를 import 해 수집 | 신규 |
| `kernel/daemon` | 스케줄러 셸: 레지스트리의 JobSpec → APScheduler, 큐 레인 4개, heavy 락, 미스파이어/캐치업 정책(JobSpec 속성), 하트비트, 잠금, 종료 훅 | `daemon.py` 의 골격 ~600줄만 |
| `kernel/web` | FastAPI 셸: 라우터 마운트, 인증 3등급(토큰/직접로컬/same-origin), **SSE 지문을 레지스트리 키로 조립**, 정적 dist | `web/app.py`·`auth.py`·`sse.py` |
| `kernel/notify` | 텔레그램 전송·outbox·수신, 명령 디스패처(명령은 기능이 등록), 메시지 렌더 베이스 | `notify/telegram.py`·`commands.py` 골격 |
| `kernel/ops` | health 러너(영역은 기능 등록), retention, STATUS 렌더러(절은 기능 등록), watchdog | `ops/`, `status.py` |
| `kernel/platform` | paths(`TRADER_HOME`), bundle, procs, filelock, tray, installer, logging | `platform/`, `logging_setup.py` |
| `kernel/cli` | typer 루트 + `_boot`, 기능 sub-app 마운트 | `cli.py` 골격 |
| `features/accounts` | 계좌 yaml·모드·LIVE 확인·연결 키·계좌 추가/삭제, `account_modes` 테이블 | `control/`, `accounts_io.py`, `secrets_io.py` |
| `features/ingest` | KIS·DART·EDGAR·뉴스·커뮤니티·거시 수집, `build_inputs`, 뉴스 저장소, 공시 중요도, 수동 입력 | `sources/` |
| `features/saveticker` | 수집기(Playwright 레인·서비스·감독·monitor) + 리더 + 데몬 잡; Chrome 확장은 저장소 루트 `extensions/saveticker/` | `saveticker/`, `sources/saveticker.py`, codex 브랜치 `extensions/` |
| `features/scan` | 정량 필터·screener·scout 발굴·테마·사건·유니버스 PIT | `scan/`, `ml/universe_pit.py` |
| `features/spot` | **Lane 구현**: 종목 수집·analyze/resume 세션·`tool checkpoint/submit-decision`·`risk/rules` 게이트·Portfolio 빌더·monitor(`check_position`·손절·트리거)·재평가·fills·reprice·reconcile·balance. **하위 의도 생산자**: hedge·cash_park·rebalance(레인이 아니라 spot 안의 intent producer) | `orchestrator/`(session 제외), `risk/` 대부분 |
| `features/regime` | 국면 3지표·히스테리시스·stance·시장 세션(market_view)·전략가 기울기·킬스위치·권리 블랙아웃 | `risk/regime.py`·`stance.py`·`killswitch.py`·`blackout.py`, `orchestrator/market_view.py` |
| `features/fo` | **Lane 구현**: 그대로 이식하되 전송·계좌 루프·기록 전용 판정은 커널 executor 코어로, leg 행은 `IdemStore` 구현체 `LegStore` | `fo/` |
| `features/scalp` | **Lane 구현**: 신호·엔진·판정자, `decide()` = PythonSignal, `reevaluate()`/`reflect()` = None | `scalp/` |
| `features/ml` | 피처 스토어·학습·레지스트리·랭커·튜닝, `rl/` 하위 | `ml/` |
| `features/evaluation` | 메모리·보정·reflect·결과 성숙·섀도 포트폴리오·성과·readiness·backtest·paper_review·일일 리포트 | `memory/`, `calibration/`, `orchestrator/reflect.py`, `ledger/shadow.py`·`performance.py`, `readiness.py`, `backtest.py` |
| `features/briefing` | 아침 브리핑(haiku 단발) — ④ 에서 확장 | `orchestrator/briefing.py` |
| `features/quotes` | 실시간 시세 허브·호가·지수·차트·종목명(`names_fill`) | `web/live_quotes.py`, `names.py`, charts 라우터 |
| `features/jobs` | 작업 대기열 요청·kind 레지스트리 소비·웹/텔레그램/CLI 요청 경로 | `jobs.py` |

각 `features/<x>/` 는 같은 골격: `feature.py`(Feature 선언 — 유일한 등록 지점) · `settings.py` · `migrations.py` · `cli.py` · `routes.py` · `jobs.py` · `messages.py` · `health.py` · `README.md`(모듈 규칙 — architecture.md 의 해당 절 이관). 테스트는 `tests/kernel/<x>/`·`tests/features/<x>/` 로 미러.

### 2.2 Lane 추상 (현물·파생·단타 공통)

조사 결과 진짜 공통은 **가장자리**(정책·orders·triggers)와 **executor 코어 8단계**이고, 게이트·포지션 모델·멱등 단위·신선도 정책은 레인마다 다르다. 그래서 Lane 은 "파이프라인 단계의 자리" 를 고정하고 구현은 플러그한다.

```python
class Lane(Protocol):
    id: str              # spot | fo | scalp
    asset: str           # stock | fo  (계좌 필터)
    def schedule(self, schedule, settings) -> list[JobSpec]      # open(cron) + tick(interval) + 큐 kind
    def allowed(self, ctx, market, today) -> str | None           # 설정·paused·국면·제안·일일 중단
    def collect(self, ctx, market, today, asof) -> list[WorkItem] # 종목 | [시장] | 후보
    def decide(self, ctx, item) -> DecisionSource                 # SessionSpec | PythonSignal | Judge
    def plan(self, ctx, decision, account, fresh) -> GatePlan     # rules.evaluate / fo.rules.evaluate 래핑
    def tick(self, ctx, market, today) -> TickResult              # 청산·유지 → OrderIntent, 트리거
    def reevaluate(self, ctx, trigger) -> SessionSpec | None      # scalp 은 None
    def reflect(self, ctx, today) -> SessionSpec | None           # 현재 spot 만
```

공유 타입(모두 `kernel/execution`):
- **`OrderIntent{lane, account_id, instrument, side, qty, order_type, price, origin, stage, idem_key, decision_ref, max_qty?, reason}`** — `is_stop_loss/is_hedge/is_reprice/is_rebalance/is_park/scalp_leg/exec_tag` 플래그 7개를 대체. `(origin, stage)` 가 체크포인트 쌍(4x_/5x_)·`orders.origin`·결정행 유무·게이트 면제 집합을 **한 표**(`ORIGIN_TABLE`)에서 결정한다(옛 코드는 3곳을 같은 순서로 맞춰야 했다).
- **`GatePlan{approved, reasons, intents, verdict_payload}`** — spot·scalp 1개, fo N 다리.
- **`ExecPolicy{mode, paused, dry_run, live_confirmed}`** 리졸버 하나 — spot 인라인 판정·fo `_record_only`·scalp `_can_trade` 통합.
- **`IdemStore`** 프로토콜 `claim(key, reserve?) / bind(order_id) / release / mark_sent` — `ExecKeyStore`(exec_locks + buy_reservations) / `LegStore`(fo leg 행).
- **`LaneContext`** 하나 — `ExecContext/FoExecContext/ScalpContext/MonitorContext/ToolContext` 공통 필드 + `lane_ext`.

레인 고유로 남는 것(플러그): 게이트 함수·Portfolio 빌더, 신선도 정책(10분 재시세 / 진입마다 재조회 / 신호 봉), 멱등 백엔드, 포지션 상태(브로커+원장 / `fo_positions` CAS / JSON), 재개 로직, 도구 표면·전제조건, 결정 스키마, monitor 규칙, fo 전용(진입 창·REPLACE·recall·`fo_verified`·정산).

세션 러너는 `run_session(spec, lane_ctx)` 로 `fo=` 인자를 없애고, kind 별 분기(`RUN_KINDS`·`agents_for`·`_stdin_payload`·`timeout_by_kind`·FAKE if 체인)는 `SessionKind` 레지스트리 항목 하나로 모은다. 조사에서 나온 불일치(`fo.session_cap` 주석 vs `fo_resume` 미집계, `58_order_park` 가 `_STAGE_KIND` 에 없음, `decision_trace` 가 43/46/47/48 생략, approve 계좌의 scalp 승인 요청)는 **이식 때 옛 동작을 보존하고 README 「알려진 차이」에 적는다** — 의미 변경은 별도 Phase.

### 2.3 기능 레지스트리 계약

`features/<x>/feature.py` 의 `FEATURE = Feature(...)` 가 유일한 등록 지점. 커널은 `registry.discover()` 로 모으고, 옛 중앙 if 체인은 전부 데이터 순회로 바뀐다.

| 필드 | 내용 (조사 §A-12 항목 전부 포함) |
|---|---|
| `jobs: list[JobSpec]` | id 템플릿·시장별/전역·cron/interval·스케줄 설정 키·enabled 술어·게이트(market/monitor/none)·heavy·미스파이어 등급(scan/idempotent/default)·캐치업(queue kind/직접/알림)·start-soon·teardown |
| `hooks` | `scan_analyze.pre/post`, `monitor.pre_stop`, `news.post_ingest`, `reflect.mature`, `daemon.bootstrap` — 실패 격리된 단계 훅(옛날엔 남의 body 를 편집) |
| `queue_kinds` | params 모델·라벨·job_id·레인(slow/fast/reeval/info)·우선순위·trading 플래그·restart-requeue·body |
| `cli: typer.Typer`, `tools: list[ToolCommand]` | 사람용 sub-app과 LLM 가시 `tool` 명령 + 전제조건 표(`StageRule`) |
| `settings_sections` | yaml 파일별 pydantic 섹션 + 파생 헬퍼 기여(`excluded_tickers`·`instrument_names`·`timeout_for`) |
| `deps` | DaemonDeps/WebDeps/LaneContext 에 기여·소비하는 필드 선언 |
| `routes: list[APIRouter]`, `sse_keys: list[SseKey]`(probe + noisy 플래그), `nav`(프런트 메뉴 항목 — 생성 파일로 내보냄) |
| `telegram_commands`(이름·untrusted 허용·help 줄), `messages` |
| `health_area`, `status_section`, `control_namespaces`(보존/만료), `sse_control_exclusions` |
| `origins: list[OriginSpec]` | 실행 출처 선언 — 체크포인트 쌍·`orders.origin`·결정행 유무·게이트 면제·멱등 정책·Portfolio 기여 |
| `session_kinds: list[SessionKind]`, `agents: list[Path]` |
| `migrations: list[Migration]` | 전역 버전 순서에 append |

계약 테스트(커널): 모든 Feature 가 필수 필드를 가짐 · 잡 id 충돌 없음 · SSE 키 ↔ 프런트 `tickOf` 키 일치(생성 파일) · origin 표 ↔ `orders.origin` CHECK ↔ 체크포인트 접두 일치 · `features/*` 간 직접 import 없음(import-linter 또는 자체 AST 테스트).

### 2.4 원장 v1 자원 모델

옛 34 버전을 읽어 **깨끗한 v1 로 다시 그린다**(데이터 이전 없음). 금액은 전부 TEXT(Decimal) 로 통일(REAL 혼재 제거). 원칙: 패치 열은 본 테이블로, `control` 의 사실상 테이블은 진짜 테이블로, 상태 열거는 CHECK 로.

| 자원 | v1 테이블 | 옛 것에서 바뀌는 점 |
|---|---|---|
| accounts | `accounts`, `account_modes`(mode·live_confirmed·updated) | `control.mode:*`·`live_confirmed:*` 승격, `asset` 기본 열 |
| decisions | `decisions`(하루 1행) + **`decision_rounds`**(라운드별 행: round·action·stop_loss·source·skipped_debate) | 재평가 덮어쓰기·`stop_floor`·`updated_at` 우회 제거; `source`·`skipped_debate` 기본 열. fo 는 `fo_decisions` 유지하되 COALESCE 유니크 → 명시 키 |
| risk_checks / approvals | `risk_checks`, `approvals`(fingerprint NOT NULL, stage·exec_key·origin·quote_price 열) | `order_json` 사이드채널 제거 |
| orders / fills | `orders`(origin·asset CHECK, amount·org_no 기본, status CHECK + `DEAD_STATUSES` 는 뷰/상수 한 곳), `fills`(dedupe 유니크) | NULL=llm 관행 제거 |
| positions | `positions_snapshot`(현물·fo 통합, `asset` 열), **`account_nav`**(nav·cash·date) | `_CASH` 의사 행·`fo_nav_snapshot`·`fo_positions_snapshot` 통합 |
| execution | **`exec_locks`** + **`buy_reservations`** | `exec_keys` 분리. fo leg 는 `fo_order_legs`(request_id 기본 열) |
| monitor | `triggers` | 그대로(kind 에 `fo:` 접두 유지) |
| regime/risk | `regime_state`, `market_views`, `killswitch_state`, `daily_markers(kind, market, date, account?)` | `control.regime:*`·`killswitch_*`·`*_done:<날짜>` 15종 승격(retention 의 날짜 파싱 삭제) |
| ops | `ops_results`(level·json·at), `job_requests`(priority 기본), `runs`, `agent_calls`, `llm_rate_limits` | |
| news/scan | `news_items`(novel·severity 기본), `theme_heat`, `universe_members`, `ticker_names`, `ticker_exchanges` | |
| evaluation | `memory_entries`(채점 열 1벌: direction_score·avoided_loss·realized_pnl_pct·post_fill_return·round·held_at_decision), `calibration_runs`, `rl_shadow`, `shadow_portfolios`(partial·def_version NOT NULL) | 구버전 `realized_return`·`alpha_vs_benchmark` 열 폐기 |
| fo | `fo_positions`, `fo_order_legs`, `fo_decisions`, `fo_flags`(verified·halted·pending_entry·…) | control 키 → 테이블 |
| control | `control`(watchlist·paused·telegram cursor 등 **진짜 설정성 플래그만**) | |

마이그레이션은 기능별 파일(`features/<x>/migrations.py`)이 `(version:int, name, sql|fn)` 을 제공하고 커널이 전역 오름차순으로 적용(추가만, 멱등·롤백 테스트는 옛 `ledger/test_db.py` 이식).

### 2.5 웹 API 자원 모델 (③ UI 재설계의 토대)

`/api/` 아래 **12 자원**, 계좌 범위는 경로 세그먼트, 쓰기는 자원에 대한 PATCH/POST(옛 `/control/*` RPC 폐지). 인증 등급은 그대로(조회 토큰 없음·루프백, 쓰기 `Bearer WEB_TOKEN`, 키 입력은 직접 로컬).

| 자원 | 대체하는 옛 엔드포인트 |
|---|---|
| `accounts` (`/accounts`, `/accounts/{id}` PATCH mode, POST/DELETE) | `/accounts`, `/control/accounts*`, `/control/mode`, `/fo/account` |
| `accounts/{id}/positions`, `/positions/overview`, `/positions/risk`, `/tax-lots` | `/portfolio*`, `/tax-lots`, `/fo/positions` |
| `decisions` (`/decisions`, `/{id}`, `/{id}/trace`, `/{id}/outcomes`, `/{id}/checkpoints/{stage}`), `markets/{m}/today` | `/decisions*`, `/today`, `/checkpoint`, `/market-checkpoint`, `/risk-checks`, `/fo/today` |
| `orders` | `/orders`, `/fo/legs` |
| `approvals` (`PATCH /approvals/{id}` approve/reject) | `/approvals`, `/control/approve|reject` |
| `jobs` (`/jobs`, POST, `DELETE /jobs/{id}`, `/runs`, `/calls`) | `/jobs`, `/control/jobs*`, `/runs`, `/calls` |
| `events` (SSE) | `/events` |
| `news` (`/news`, `/news/events`, `/news/themes`, `/news/manual` POST) | `/news`, `/system/events`, `/system/theme`, `/control/manual-inputs` |
| `quotes` (`/quotes`, `/indices`, `/book`, `/charts*`, `/names*`, `/features*`) | 동일 |
| `settings` (`GET /settings`, `PATCH /settings/{watchlist|usage-throttle|paused}`, `/settings/secrets*`) | `/system/settings`, `/control/{watchlist,usage-throttle,pause,resume,secrets*}` |
| `system` (`/system/{health,status,usage,ops,daemon,regime,saveticker,scalp}`, `POST /system/saveticker/restart`, `/system/bootstrap-token`) | 동일 |
| `analytics` (`/analytics/performance*`, `/ml/*`, `/rl/*`, `/fo/{scorecard,report,summary}`) | 신설 |

SSE: 지문 키는 기능 등록(§2.3)이고, 커널이 `web/src/generated/sse-keys.ts` 를 생성해 프런트 `tickOf` 가 그 타입만 쓴다(계약 테스트가 동기 확인). `CSV ?format=csv` 유지. 프런트는 이 단계에서는 **기존 페이지를 자원 API 로 재배선만**(③ 에서 시각 재설계).

### 2.6 규칙의 코드화 (CLAUDE.md 다이어트)

- 산문 규칙 → 다음 중 하나로: ① 타입(`OrderIntent`·`ExecPolicy`·`SessionKind`) ② 선언 표(`ORIGIN_TABLE`·`StageRule` 전제조건·JobSpec 속성) ③ 계약 테스트(§2.3) ④ 기능 `README.md`(모듈 규칙 — `architecture.md` 절 이관).
- 새 `CLAUDE.md` ≤ 16KB: 목적 1단락 · 실행 명령 표 · **파일 2개 이상에 걸친 불변식만**(LLM 은 도구만, 주문은 executor 코어만, dry_run 은 env, 실전 auto 는 LIVE 확인, 룩어헤드, 금지사항, 코딩 규칙) · "기능을 더하는 법 = `features/<x>/feature.py` 한 곳" · 포인터.
- 옛 `CLAUDE.md`·`architecture.md` 의 각 절은 이식 체크리스트의 입력이며, 대응 테스트가 있으면 산문을 지우고 없으면 README 로 옮긴다.
- `docs/TODO.md` 의 "사용자 확인 대기" 실기 항목(KIS 주문 테스트·텔레그램 승인·번들 더블클릭 등)은 새 저장소 TODO 로 승계한다(옛 저장소에서 아직 미확인).

### 2.7 공개 저장소 위생·CI

- `.gitignore`: `config/`, `secrets/`, `state/`, `dist/`, `build/`, `web/dist`. `templates/config/*.yaml` 만 커밋(자리표시자 `00000000`, 이메일 없음). `trader init` 이 복사. 옛 `test_cli_app` 의 패리티 테스트는 "templates 가 스키마를 통과한다" 로 바뀐다(config 가 저장소에 없으므로).
- 수집 원문 fixture 금지(합성만). 옛 `tests/sources/fixtures/saveticker/` 는 이미 합성.
- CI `ci.yml`: PR·main push — ruff · pytest(**ubuntu + windows 매트릭스**, Windows 동일 실행이 요구사항이고 옛 저장소가 Windows 12건을 고친 전력) · 웹 typecheck/test/build · 계약 테스트. `build.yml`: `v*` 태그 — 3 OS 번들·릴리스 첨부(옛 것 계승). 브랜치 보호: main 은 PR + CI 녹색만.
- 커밋 트레일러·브랜치 규칙: 마일스톤 단위 `feat/m<N>-<이름>`, PR 대상 main.

### 2.7-1 빌더 검토 뒤 확정한 것

- **기능 간 import 규칙(내 결정)**: 3층 — `kernel` ← **서비스 기능**(accounts·ingest·saveticker·scan·regime·ml·evaluation·briefing·quotes·jobs) ← **레인**(spot·fo·scalp). import 는 아래쪽으로만. 다른 기능을 쓸 때는 그 기능의 `api.py` 공개면만(내부 모듈 금지), 서비스 기능끼리도 `api.py` 로만, 레인은 어떤 기능도 import 하지 못한다(레인에 필요한 것은 `deps` 주입). 커널에는 Protocol·DTO 만 두고 구현체는 올리지 않는다. 계약 테스트(AST 스캔)가 허용 목록을 강제한다. 옛 코드의 지연 import(`risk` 16·`sources` 14·`scan` 11·`ml` 13·`memory` 9·`saveticker` 3)는 이식 때 전부 `api.py` 경유로 바뀐다.
- **리허설 날 자원 분리(내 결정)**: 새 데몬은 `notify.enabled=false`(텔레그램 폴링·알림 끔, 명령은 옛 데몬이 계속 받음), 웹 포트 8788, KIS 레이트 DB·토큰 캐시는 `TRADER_KIS_SHARED_DIR` env 로 옛 `state/` 의 `kis_rate.db`·`kis_token_*.json` 을 **공유**(앱키당 1초 한도는 두 프로세스 합산이어야 함). 커널 `broker` 가 이 override 를 지원(M1). "하루 무사고" 는 잡 표 전부 실행·`runs` 오류 0·운영 점검 ok·대시보드 갱신으로 판정하고, 텔레그램 왕복은 전환 직후 `notify-test`·`/status` 로 확인한다.
- **M11 완료 기준(내 결정)**: 백엔드만 — 12 자원 API·SSE 키 생성·계약 테스트·옛 `tests/web/*` 의 API 테스트 이식. 프런트는 옛 페이지를 재배선하지 않고, ③ 웹 UI 브레인스토밍(M11 착수 전) 결과로 **새 UI 를 바로 구현**(M11b). vitest 39 는 순수 함수(`features.ts`·`orders.ts`·`glossary`·`tokens`·`nav`)만 이식.
- **Windows CI(내 결정)**: `ci.yml` pytest 를 ubuntu + windows 매트릭스로. Playwright/Chromium 의존 테스트는 옛날처럼 `importorskip`/skip.
- **교체 시점 보유(사용자)**: 모의 계좌는 이미 초기화됨 → 새 원장 0 에서 시작, 포지션 입양 경로 불필요. 실전 계좌는 `off` 그대로.
- **옛 저장소 `AGENTS.md`·`.codex/`(사용자)**: 함께 커밋 — 세이브티커 머지 PR 에 `chore(codex)` 커밋으로 포함.

### 2.8 빌더에게 맡기는 것

- `kernel/` 내부 모듈 경계의 세부, `Feature` dataclass 의 정확한 필드 타입, 생성 파일 방식(`sse-keys.ts`·nav).
- `decision_rounds` 와 메모리 엔트리의 조인 키 세부, `daily_markers` 의 인덱스.
- 테스트 디렉터리 미러 규칙과 공용 fixture(`project`·`conn`·`make_deps`·FAKE) 의 커널 이전 위치.
- import 경계 검사 도구 선택(import-linter vs AST 테스트).

---

## §3. 이식 순서 (마일스톤) — 각 M 은 브랜치 1개·PR 1개·옛 테스트 이식 통과가 완료 기준

| M | 내용 | 옛 테스트 → 새 위치 | 완료 기준 |
|---|---|---|---|
| M0 | 저장소 생성(public)·`pyproject`·uv·ruff·`.gitignore`·templates·CI 2개·`kernel/{config,platform,registry,ledger 코어,daemon 셸,web 셸,cli 루트}`·설계 문서 커밋·**계약 테스트 5종 선행**(원장 v1 스키마·agents 공통 규칙 패리티·`allowedTools==tool_command()`·templates 스키마 통과·SSE 키 동기) | `test_config*`, `ledger/test_db`, `platform/*`, `test_shared_helpers` | CI 녹색, `trader init`·`selfcheck` 동작 |
| M1 | `kernel/broker` KIS 전부 + 레이트리미터 | `broker/*` (17 파일) | `kis ping` 코드 경로 테스트 통과 |
| M2 | `features/accounts`(모드·LIVE·키·추가삭제) + `kernel/notify` 전송·outbox | `control/*`, `accounts_io`, `secrets_import`, `notify/test_telegram` | |
| M3 | `features/ingest` + `features/saveticker`(수집기·리더·`extensions/`·데몬 잡) | `sources/*`, `saveticker/*`, `test_daemon_news/disclosure/saveticker` | |
| M4 | `kernel/session`(SessionKind 레지스트리·FAKE) + `kernel/execution` 코어 + **`features/spot` analyze 경로**(tools·checkpoints·게이트·Portfolio·승인·주문) — 가장 큼. **골든 회귀**(`stock_regression_phase25.json`) 1회 의도적 재기준 | `agents/*`, `orchestrator/{session,quick,tools,summary,run_store,executor,approval_fingerprint}`, `risk/{rules,stops,ticks,horizon,costs}`, `test_cli_analyze`, `test_stock_regression_fo` | `FAKE_CLAUDE=1 trader analyze --dry-run` 체크포인트 일치 |
| M5 | spot monitor·resume·손절·fills·notice·reconcile·balance·reprice | `orchestrator/{monitor,resume,fills*,reconcile,reprice,balance_dispatch,notice}`, `test_daemon_queue` | |
| M6 | `features/regime`(국면·stance·시장 세션·킬스위치·블랙아웃) + spot 의도 생산자(hedge·cash_park·rebalance) + scan 의 테마·사건 | `risk/{regime,killswitch,blackout}`, `orchestrator/{hedge,market_view,cash_park,rebalance,market_tools}`, `test_daemon_hedge/theme`, `scan/test_{theme,events}` | |
| M7 | `features/scan`(quant·screener·discovery·유니버스 PIT) + `features/ml`(피처·학습·레지스트리·랭커·rl) + `features/jobs` 큐 | `scan/*`, `ml/*`, `rl_*`, `test_jobs` | |
| M8 | `features/evaluation`(메모리·보정·reflect·섀도·성과·readiness·backtest·paper_review·일일 리포트) + `features/briefing` | `memory/*`, `calibration/*`, `orchestrator/{reflect,briefing}`, `ledger/{shadow,performance,lots,backup,open_window}`, `test_readiness/backtest` | |
| M9 | `features/scalp` Lane | `scalp/*`, `test_daemon_scalp` | |
| M10 | `features/fo` Lane(`LegStore`·커널 executor 코어 위) | `fo/*`(20 파일), `test_*_fo`, `test_daemon_fo`, `test_cli_fo` | |
| M11 | `features/quotes` + 웹 API 12 자원 + SSE 키 생성 파일 + 계약 테스트 (**백엔드만**) | `web/*`(18) API 테스트 | 모든 자원 엔드포인트 테스트 통과, 옛 엔드포인트 대응표 100% |
| M11b | **③ 웹 UI 재설계 결과 구현**(별도 브레인스토밍·플랜) | vitest 순수 함수 5종 이식 | 새 UI 로 대시보드·주문·승인·설정·잡 화면 동작 |
| M12 | `kernel/ops`(health·watchdog·status·retention) + CLI 전체 점검 + 번들(`packaging/build.py`·`build.yml`) + `CLAUDE.md` 다이어트 최종 + 전환 리허설 | `ops/*`, `test_cli_*` 나머지, `test_packaging_build`, `test_status/usage` | `FAKE_CLAUDE=1 trader daemon --dry-run` 하루 무사고 → 사용자가 `install` 교체 |

③ 웹 UI 재설계 브레인스토밍은 **M11 착수 전**에 하여 M11b 가 바로 새 UI 를 만든다. ④ MCP·브리핑·복기는 M8 이후 언제든 독립.

## 다음 단계 (승인 뒤, 참고)

1. §1 실행 — 옛 저장소 세이브티커 머지 2건 + `chore(codex)` 커밋 + 검증.
2. `superpowers:writing-plans` 로 **M0 플랜**부터 작성(설계 문서 커밋 포함). M 마다 플랜 1개, 실행은 `subagent-driven-development` 또는 `executing-plans`.
3. M10 완료 시점에 ③ 브레인스토밍, M8 이후 ④ 브레인스토밍을 각각 별도 세션으로.

## 검증 (end-to-end)

- 매 M: `uv run pytest -q` · `uv run ruff check src tests` · `cd web && npm test -- --run && npm run typecheck && npm run build` · CI 녹색(ubuntu+windows).
- 계약: 레지스트리 계약 테스트·import 경계 테스트·SSE 키 동기 테스트·원장 v1 멱등/롤백 테스트.
- 동등성: 골든 회귀 스냅샷(M4 재기준 뒤 고정), `FAKE_CLAUDE=1 trader analyze --ticker 005930 --dry-run` 체크포인트 집합 비교, `trader readiness`·`status` 출력 비교.
- 운영 리허설(M12): `FAKE_CLAUDE=1 uv run trader daemon --dry-run` 하루 — 잡 표 전부 실행·알림 정상·대시보드 갱신·`uv run trader watchdog`. 번들은 `v0.2.0` 태그로 CI 빌드 → 사용자가 `Trader.app` 더블클릭 확인.
- 옛 저장소 §1: 머지 뒤 main 에서 전체 테스트·린트·웹 테스트 통과.
