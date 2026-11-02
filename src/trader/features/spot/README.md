# spot

## 책임

- 현물(KR·US) 종목 분석 레인(tier=lane): 종목 1개를 분석해 결정(`30_decision`)을 만들고, 계좌마다 리스크를 판정해 주문 또는 승인 요청까지 보낸다. 리스크·주문은 파이썬 코드가 강제하고 LLM 은 `trader tool` 도구만 쓴다.
- 설정: `settings.yaml` 의 `risk`·`debate_rounds`·`debate`(`settings.py`: `RiskLimits`·`DebateCfg`).
- 원장 v6 테이블(`migrations.py`/`ledger.py`): `decisions`(날짜·시장·종목당 1행, 금액·비중은 TEXT), `decision_rounds`(라운드별 행, 0 = 최초 결정, `31_decision_r<n>` 은 M5 재평가), `analyst_reports`, `debate_turns`, `ticker_names`, `ticker_exchanges`. 체크포인트(`10_analyst_<role>`, `20_debate_r<n>_<side>`, `30_decision`)를 `ledger.ingest_checkpoint` 가 멱등 적재한다.
- 세션: `session_kinds.py` 의 `ANALYZE`(`SessionKind`)·`AGENTS`(애널리스트 4 + bull/bear 리서처 + trader, md 는 `agents/`), `analyze.py`(입력 수집 → 세션 → 결정), `fake.py`(`FAKE_CLAUDE=1` 흉내).
- 도구: `tool_cli.py` 의 `TOOLS`(`trader tool status|collect|checkpoint list|read|write|bundle|summarize|submit-decision`), 구현은 `tools.py`, 단계 전제조건은 `WRITE_RULES`·`SUBMIT_RULE`(`StageRule`, fnmatch glob).
- 게이트·실행: `rules.py`(리스크 판정)·`costs.py`·`stance.py`·`stops.py`·`horizon.py`·`tradability.py`·`summary.py`(수렴 감시·합의 토론 생략), `executor.py`(`ExecContext`, 계좌별 판정 → 멱등 claim → 승인 요청 또는 `execution.core.transmit`, `ORIGINS`).
- 운영 경로: `cli.py`(`trader spot …`), `telegram.py`(`/approve`·`/reject`), `jobs.py`(`approvals_expire` 잡, interval 60), `messages.py`(`approval_line` — 종목 표기는 spot 소유).

## 공개면 (`api.py`)

없음(`__all__ = []`). 레인은 다른 기능이 쓸 면을 두지 않는다. 같은 기능 안에서는 `schemas`(스키마·`schema_for`), `ledger`(결정 저장소), `labels`(`label`·`resolve`), `settings`(`RiskLimits`·`DebateCfg`)를 직접 쓴다. 외부에 보이는 면은 `FEATURE` 선언(`feature.py`)과 아래 CLI 다.

CLI(`uv run trader spot --help` 로 확인):

- `trader spot analyze --ticker … [--market KR|US --exchange NAS|NYS|AMS --asof … --dry-run --skip-collect]` — 종목 하나. `--dry-run` 은 판정·기록까지만이고 주문·승인 요청이 없다. 이미 있는 단계는 건너뛰므로 재실행하면 빠진 단계부터 이어간다.
- `trader spot approvals list|approve <id>|reject <id>` — `list` 는 만료분을 정리한 뒤 대기 요청을 보여 준다.
- `trader spot exec-key list|release <key> --note <10자 이상>` — 보낸 뒤(`sent_at`) 주문 기록 전에 끊긴 실행 키는 영구 선점이라, 사람이 KIS 주문 유무를 확인한 뒤 사유와 함께 푼다. 해제 기록은 control `exec_key_released:<key>`.
- 텔레그램 `/approve <id> [실전]`·`/reject <id>`. 실전 계좌 승인은 확인어가 없으면 실행하지 않는다.
- LLM 전용 `trader tool …`(위 도구 목록). 출력은 JSON 1줄.

## 규약

- 레인은 `trader.kernel.*` 만 import 한다. 계좌·수집 같은 서비스 기능은 `Boot.ports`(`accounts`·`inputs`)로 주입받는다(`registry.import_violations`). 지연 import 로 다른 기능에 닿지 않고 전부 포트·커널을 경유한다.
- 모든 주문·승인은 `kernel/execution` 코어를 지난다. 계좌 모드·일시정지는 `ExecContext.policy`·`paused` 콜백(accounts 포트)으로 받는다. 알림·로그에는 계좌 id 만 쓴다. `dry_run` 은 인자가 아니라 env `TRADER_DRY_RUN` 하나로 하위 프로세스(LLM 세션)까지 전달한다.
- 스키마 모델은 `extra="forbid"`. 가격은 양수만, `target_weight` 는 0~0.25. 쓰기 허용·전제조건은 `WRITE_RULES`(glob) + 스키마·파일명↔페이로드 일치·`debate_rounds` 상한이 강제한다.
- 원장 쓰기는 호출마다 commit 하고 행 단위로 멱등이다(같은 단계 재적재 = 덮어쓰기, 행 id 유지). 로거는 `trader.spot`.
- `settings.yaml` 키를 더하면 `templates/config/settings.yaml` 도 같이 바꾼다.
- `FAKE_CLAUDE=1` 없이 `trader spot analyze` 를 돌리지 않는다(구독 호출). 운영 메모: `FAKE_CLAUDE=1 trader spot analyze` 를 KIS 키 없이 돌리면 `00_inputs` 에 시세가 없어 `submit-decision` 이 fail-closed 로 거부하고, 체크포인트 11개 + exit 1 로 끝난다(의도된 동작). 키가 있거나 `FakeMarket` 을 주입하면 13개다.

## 테스트

`tests/features/spot/` — 설정·스키마·원장·기능 등록·포트 조립, 리스크 규칙·tradability·summary, 도구·`StageRule`, executor·승인 지문·`exec-key`·`approvals` CLI·텔레그램·잡, FAKE 세션·analyze CLI, 골든 회귀(`test_regression.py`, `stock_regression_v1.json` 로 1회 재기준). 커널 쪽은 `tests/kernel/{session,execution,lane}/`. 포트 구현체 테스트는 `tests/features/accounts/test_ports.py`, `tests/features/ingest/test_ports.py`.

## 알려진 차이 (옛 저장소 대비)

CLI·명령
- 최상위 `trader analyze`·`trader collect` 별칭이 없다(기능 sub-app 일관): `trader spot analyze`, `trader ingest collect`. 옛 `approvals` 도 `trader spot approvals`. 스펙 M4 행의 `trader analyze --dry-run` 은 `trader spot analyze --dry-run` 으로 읽는다.
- `trader status`(마지막 결정 표시)는 M12.
- 텔레그램 핸들러는 `CommandContext.ports["accounts"]` 로 계좌·브로커·정책을 받는다(포트가 없으면 안내만, 주문 없음).
- `approvals_expire` 잡은 `boot` 를 부르지 않고 `ctx.conn_factory()`·`ctx.now`·`ctx.notifier` 로 계좌·브로커 없는 최소 `ExecContext` 를 만든다(만료는 conn+now 만 쓴다).
- `approval_line` 은 spot `messages.py` 가 소유한다(커널 `notify.messages` 의 ENV·SIDE·`money`·`kst_hhmm` 재사용).
- 체결 동기화(`fills.py`, `trader spot fills sync`, `fills_sync` 잡·큐 kind): 파생(`fo`) 계좌 분기는 M10 까지 없다(현물 계좌만). 큐 `fills_sync` 본문 `fills_then_balance` 는 체결 동기화 뒤 장 밖이어도 잔고를 바로 다시 읽는다(잔고 실패는 체결 결과를 가리지 않음).
- balance(`balance.py`, `balance_refresh` 잡, `trader spot balance refresh`)·reconcile(`reconcile.py`, `{market}_reconcile` 잡·큐 kind `reconcile`, `trader spot reconcile`): 옛 동작에서 섹터 자동분류(`_classify`)·킬스위치 미체결 BUY 회수(`cancel_buys`)·cash_park/헤지 제외·메모리 브레이커 인자·fo 분기를 뺐다. `snapshot_age` 는 보유 0건 계좌도 신선도가 보이도록 `account_nav` 를 본다. 대사 알림은 (계좌·시장·날짜) 플래그가 이미 있으면 다시 보내지 않는다(하루 1회). `reconcile.us` 는 뉴욕 시각(`tz: America/New_York`).

세션·컨텍스트
- 주문 알림 제목 꼬리 표 `_STAGE_KIND`(커널 `execution.core`)는 `order_stage` 를 `_r` 로 잘라 찾는 옛 동작을 그대로 둔다. 그래서 `_r` 로 시작하는 꼬리가 있는 `52_order_reprice`·`53_order_rebalance` 는 꼬리가 비고, 실제로 꼬리가 붙는 것은 `51_order`(재평가)·`55_order_sl`(손절) 등 `_r` 가 없는 단계다(M4 의 spot 이 쓰는 것은 51·55).
- `trader_context` 는 quote·risk_limits·costs_bp·portfolio·holding 만 싣는다. memory(M8)·calibration(M8)·quant(M7)·regime(M6)·theme(M6)·market_view(M6) 컨텍스트는 주입하지 않는다.
- `AnalystRole` 에서 `"quant"` 를 뺐다(`ANALYST_ROLES` 4종과 일치, quant 레인은 M7).
- `ask_json`·`probe_usage`(`quick.py`)는 이식하지 않았다(M8). `resume` 세션·`31_decision_r<n>` 재평가는 M5. 옛 `ingest_checkpoint` 의 `stop_floor` 규칙(같은 날 재평가 시 BUY/ADD/HOLD 손절가 최고값 유지)도 재평가가 생기는 M5 로 이연.
- `paths.agents_dir()` 는 쓰지 않는다(에이전트 md 는 `features/spot/agents/`, 번들 정리는 M12).
- `common_rules`(커널 `session.agents`) 는 옛 정규화(끝 마침표 제거)를 복원했다 — 옛 md 간 마침표 위치 차이를 계약 테스트가 허용하기 위함.

실행·승인
- 승인 실행의 `pending → approved` 조건부 UPDATE 는 `expires_at > now` 도 요구한다 — 재판정(`recheck`) 도중 만료되면 주문 없이 `expired`(옛 저장소는 실행 시작 때만 만료를 봤다).
- 계좌 포트는 현물 계좌만 내놓고 정책은 `account_mode`(실전 파생 항상 off)다.
- 승인 재판정에서 killswitch·bear 재검사를 하지 않는다(M6). 승인 계좌가 설정에 없으면 `expired` 로 처리한다. 트리거 가격은 M5.
- `submit-decision` 결과 dict 에 `memory_entry_id` 가 없다(M8). `memory_entries` 단언은 삭제했다(M8).
- `approvals.fingerprint` 는 NOT NULL 이라 NULL 지문 승인은 만들 수 없다(옛 `test_approve_legacy_null_fingerprint_passes` → `test_approval_without_fingerprint_cannot_be_created`). 옛 pending 승인의 지문 불일치는 옛 데이터를 옮기지 않으므로 해당 없음.
- 메모리 서킷브레이커(M8)·RL 사이저(M7)·국면/전략가 기울기·킬스위치·섹터·테마·권리 일정(M6)·stop/origin 5건(M5)은 아직 없다.
