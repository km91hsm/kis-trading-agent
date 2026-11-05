# spot

## 책임

- 현물(KR·US) 종목 분석 레인(tier=lane): 종목 1개를 분석해 결정(`30_decision`)을 만들고, 계좌마다 리스크를 판정해 주문 또는 승인 요청까지 보낸다. 리스크·주문은 파이썬 코드가 강제하고 LLM 은 `trader tool` 도구만 쓴다.
- 설정: `settings.yaml` 의 `risk`·`debate_rounds`·`debate`(`settings.py`: `RiskLimits`·`DebateCfg`).
- 원장 v6·v9 테이블(`migrations.py`/`ledger.py`): v9 `triggers`(장중 감시 트리거 — 종류·가격·처리 시각·세션 id), v6 `decisions`(날짜·시장·종목당 1행, 금액·비중은 TEXT), `decision_rounds`(라운드별 행, 0 = 최초 결정, `31_decision_r<n>` 은 M5 재평가), `analyst_reports`, `debate_turns`, `ticker_names`, `ticker_exchanges`. 체크포인트(`10_analyst_<role>`, `20_debate_r<n>_<side>`, `30_decision`)를 `ledger.ingest_checkpoint` 가 멱등 적재한다.
- 세션: `session_kinds.py` 의 `ANALYZE`(`SessionKind`)·`AGENTS`(애널리스트 4 + bull/bear 리서처 + trader, md 는 `agents/`), `analyze.py`(입력 수집 → 세션 → 결정), `fake.py`(`FAKE_CLAUDE=1` 흉내).
- 도구: `tool_cli.py` 의 `TOOLS`(`trader tool status|collect|checkpoint list|read|write|bundle|summarize|submit-decision`), 구현은 `tools.py`, 단계 전제조건은 `WRITE_RULES`·`SUBMIT_RULE`(`StageRule`, fnmatch glob).
- 게이트·실행: `rules.py`(리스크 판정)·`costs.py`·`stance.py`·`stops.py`·`horizon.py`·`tradability.py`·`summary.py`(수렴 감시·합의 토론 생략), `executor.py`(`ExecContext`, 계좌별 판정 → 멱등 claim → 승인 요청 또는 `execution.core.transmit`, `ORIGINS`), `excluded.py`(`excluded_tickers` — 헤지 ETF ∪ 현금 운용 ETF, 종목 수·섹터 계산에서 제외).
- 포트(`ports.py`, M9): `ports["stock_exec"]` = `StockExecPortImpl`(커널 `execution.ports.StockExecPort`) — 다른 레인(scalp)이 받는 현물 집행 서비스. `execute` 는 spot `executor.execute`(게이트·승인·멱등·기록, origin `scalp` 은 spot `ORIGINS` 소유), `cancel` 은 커널 `core.cancel`, `sync_fills` 는 `fills.sync_fills` 를 그대로 지나고 호출마다 `ExecContext` 를 조립할 뿐이다. 레인이 다른 레인에 포트를 공급하는 첫 선례.
- 운영 경로(M5): `cli.py`(`trader spot …`), `telegram.py`(`/approve`·`/reject`), `jobs.py`(데몬 잡·큐 kind·체결통보 리스너 조립), `messages.py`(`approval_line` — 종목 표기는 spot 소유).
- 체결·잔고·대사: `fills.py`(외부 체결 행·부분/전량·만료·모호 플래그), `balance.py`(`positions_snapshot`·`account_nav` 갱신), `reconcile.py`(브로커 보유 vs 스냅샷 대사 — 스냅샷만 덧쓰고 주문은 유지, 하루 1회 알림), `notice.py`(KIS 체결통보 웹소켓 리스너 — 공시 알림이 아니다).
- 장중 감시·재평가: `monitor_rules.py`(손절·익절·급등락·무효화·정기 순수 규칙), `monitor.py`(`monitor_once` — 손절 집행·트리거 기록·재평가 큐 등록), `resume.py`(`RESUME` 세션 종류·`31_decision_r<n>` 재평가·`stop_floor`·트리거 가격 게이트), `reprice.py`(아침 미체결 재호가, 하루 1회), `hedge.py`(국면 헤지 의도 생산자 — `rebalance`·`hedge_tick`, bear 면 인버스 ETF 진입·벗어나면 전량 해제, 운영일 1회 `hedge_done:<시장>:<날짜>`, 설정·국면·제안·상품은 `ports["regime"]`), `cash_park.py`(현금 단기금리 ETF 운용 — `run`·`cash_park_tick`, bear 면 남는 현금 매수·벗어나면 전량 해제, 상태 `cash_park_done:KR:<날짜>`, 국면은 `ports["regime"].state`), `locks.py`(`lock_for(market, ticker)` 종목 락 — 재평가와 분석이 겹치지 않게; M7 `analyze_ticker` 가 공유한다).
- 주간 리밸런스: `rebalance.py`(`drift_legs`·`target_decision`·`run`·`rebalance_body`·`rebalance_request` — 최근 결정 `target_weight` 대비 `rebalance.band_pct` 밖 보유만 TRIM 먼저·ADD 나중으로 `executor.execute(is_rebalance=True)`. 설정 절 `rebalance`(`enabled=true`·`band_pct=0.05`·`lookback_days=30`), 스케줄 절 `rebalance`(`kr` `{14:30, fri}`·`us` `{15:00, America/New_York, fri}`)).

## 공개면 (`api.py`)

없음(`__all__ = []`). 레인은 다른 기능이 쓸 면을 두지 않는다. 같은 기능 안에서는 `schemas`(스키마·`schema_for`), `ledger`(결정 저장소), `labels`(`label`·`resolve`), `settings`(`RiskLimits`·`DebateCfg`)를 직접 쓴다. 외부에 보이는 면은 `FEATURE` 선언(`feature.py`)과 아래 CLI 다.

CLI(`uv run trader spot --help` 로 확인):

- `trader spot analyze --ticker … [--market KR|US --exchange NAS|NYS|AMS --asof … --dry-run --skip-collect]` — 종목 하나. `--dry-run` 은 판정·기록까지만이고 주문·승인 요청이 없다. 이미 있는 단계는 건너뛰므로 재실행하면 빠진 단계부터 이어간다.
- `trader spot monitor --market KR|US [--once --interval 60 --dry-run]` — 보유종목 장중 점검(손절 즉시 매도, 트리거 → 인라인 재평가 세션). 틱마다 JSON 한 줄. 자동 주문이 나가므로 사용자 전용. 데몬은 `{market}_monitor` 잡(게이트 `monitor`, 주기 `schedule.yaml <시장>.monitor.interval_s`)이 같은 `monitor.monitor_once` 를 부르고 재평가는 큐 kind `reevaluate` 로 넘긴다.
- `trader spot fills sync [--account <id> --date YYYY-MM-DD]` — 계좌별 체결을 원장에 적재(멱등). 계좌마다 JSON 한 줄, 조회 실패 시 exit 1.
- `trader spot balance refresh [--market KR|US --force]` — 브로커 잔고를 읽어 `positions_snapshot` 갱신(조회만). 시장별 JSON 한 줄.
- `trader spot reconcile --market KR|US` — 브로커 보유 vs 스냅샷 대사(불일치는 스냅샷만 보정, 주문 유지). 계좌·시장마다 JSON.
- `trader spot approvals list|approve <id>|reject <id>` — `list` 는 만료분을 정리한 뒤 대기 요청을 보여 준다.
- `trader spot exec-key list|release <key> --note <10자 이상>` — 보낸 뒤(`sent_at`) 주문 기록 전에 끊긴 실행 키는 영구 선점이라, 사람이 KIS 주문 유무를 확인한 뒤 사유와 함께 푼다. 해제 기록은 control `exec_key_released:<key>`.
- 텔레그램 `/approve <id> [실전]`·`/reject <id>`. 실전 계좌 승인은 확인어가 없으면 실행하지 않는다.
- LLM 전용 `trader tool …`(위 도구 목록). 출력은 JSON 1줄.

## 데몬 잡·큐 표 (`jobs.py` 선언)

잡(`JobSpec`, 스케줄러가 직접 돌림. `heavy`=False, 전부 `per_market` 인 것만 id 의 `{market}` 이 소문자 시장 코드로 치환):

| id | kind | trigger | schedule_key | gate | catch_up | 비고 |
|---|---|---|---|---|---|---|
| `approvals_expire` | `approvals` | interval 60s | — | none | none | misfire `idempotent`, start_soon 30s |
| `fills_sync` | `fills_sync` | cron | `fills_sync.times`(리스트: `16:10`·`07:10`) | none | queue | 요일 `mon-sat`, misfire `idempotent`. 놓치면 큐 `fills_sync` 로 따라잡기 |
| `balance_refresh` | `balance_refresh` | interval(기본 300s) | `balance_refresh.interval_s` | none | none | misfire `idempotent`. 장중 시장은 매 틱, 장 밖은 30분 넘은 스냅샷만. `ports["regime"]` 가 있으면 실제 NAV 로 킬스위치(`risk.killswitch_drawdown_pct`)를 관측하고, dry-run 이 아니면 장중 시장에서 켜진 계좌의 당일 미체결 BUY 를 거둔다(헤지·현금 운용 ETF 제외, 옛 `on_killswitch`) |
| `{market}_reconcile` | `reconcile` | cron | `reconcile.{market}`(`reconcile.kr` `15:45` / `reconcile.us` `{time, tz}`) | market | queue | 요일 `mon-fri`(시장 tz), misfire `idempotent`. 대사 전에 체결 동기화를 먼저 돈다 |
| `{market}_rebalance` | `rebalance` | cron | `rebalance.{market}`(`rebalance.kr` `{time 14:30, days fri}` / `rebalance.us` `{time 15:00, tz America/New_York, days fri}`) | market | queue | `enabled=settings.rebalance.enabled`, 요일 `fri`, misfire `idempotent`. 운영일 1회(`control.rebalance_done:<시장>:<날짜>`), 일시정지면 플래그 없이 건너뜀. 주문을 시도하면 `[리밸런스]` 알림 |
| `{market}_monitor` | `monitor` | interval(기본 60s) | `{market}.monitor.interval_s` | monitor | none | start_soon 20s. 재평가는 큐 `reevaluate` 로 넘기고, 같은 틱에서 국면 헤지(`hedge.hedge_tick`, 하루 1회 — 결과 키 `hedge`, 실패는 `[헤지 실패]` 알림 + `hedge.error`)와 KR 이고 `cash_park.enabled` 면 현금 운용(`cash_park.cash_park_tick` — 결과 키 `cash_park`, 실패는 알림 없이 `cash_park.error`)과 개장 + `risk.reprice.after_min` 뒤 하루 1회 재호가(`reprice`)를 시도. `excluded_tickers` 보유는 점검하지 않고 결과 `skipped_excluded` 로 센다 |
| `notice_listener` | `notice_listener` | interval 30s(코드 고정) | — | none | none | `enabled=settings.fill_notice.enabled`(기본 False), teardown `notice_stop`. env 별 리스너 1개를 보장 |

큐 kind(`QueueKind`, `trader jobs kinds`·`trader jobs run <kind>`·웹·체결통보·모니터가 `job_requests` 에 등록):

| kind | 레인 | priority | trading | restart_requeue | params | 본문 |
|---|---|---|---|---|---|---|
| `fills_sync` | fast | 0 | False | True | 없음(`EmptyParams`) | `fills_then_balance` — 체결 동기화 뒤 장 밖이어도 잔고를 바로 다시 읽음 |
| `reconcile` | fast | 0 | False | True | `market`(선택) | `reconcile_request` |
| `rebalance` | fast | 0 | True | False | `market`(선택, 없으면 전 시장) | `rebalance_request` — 시장별 `{market: 결과}`, 일시정지면 `skipped` 만 |
| `approval_notice` | info | 0 | False | False | `approval_id`·`action`(`reject`만)·`actor`·`channel` (`extra=forbid`) | `approval_notice_body` — 웹 거부 뒤 텔레그램 `[거부]` 알림(`messages.reject_notice`, `/reject` 와 같은 문구). 알림기가 없으면 `skipped` |
| `reevaluate` | reeval | 10 | True | False | `market`·`ticker`·`exchange`·`trigger_id`·`trigger_ids` (`dry_run` 칸 없음, `extra=forbid`) | `reevaluate_request` — 트리거 확인·뉴스면 재수집·종목 락·세션·형제 트리거 마감 |

게이트는 accounts 가 `Feature.gates` 로 공급한다: `market`(일시정지·주말·휴장), `monitor`(market + schedule 의 `<시장>.monitor.start/end` 창). `news_collect` 는 ingest 의 interval 잡이고 spot 큐 kind 가 아니다. 데몬 기동 때 커널 내장 잡 `telegram_poll` 과 큐 틱 4개(`job_queue`·`_fast`·`_reeval`·`_info`)가 함께 등록된다(`docs/architecture.md` daemon).

## 규약

- 레인은 `trader.kernel.*` 만 import 한다. 계좌·수집·국면 같은 서비스 기능은 `Boot.ports`(`accounts`·`inputs`·`regime`·`scan`·`scalp`)로 주입받는다. `ExecContext.ports`·`ToolContext.ports` 에 `regime` 이 없으면 국면 bull·기울기 0·킬스위치/블랙아웃 없음, `scan` 이 없으면 테마 규칙 생략으로 본다(`registry.import_violations`). 지연 import 로 다른 기능에 닿지 않고 전부 포트·커널을 경유한다.
- 모든 주문·승인은 `kernel/execution` 코어를 지난다. 계좌 모드·일시정지는 `ExecContext.policy`·`paused` 콜백(accounts 포트)으로 받는다. 알림·로그에는 계좌 id 만 쓴다. `dry_run` 은 인자가 아니라 env `TRADER_DRY_RUN` 하나로 하위 프로세스(LLM 세션)까지 전달한다.
- 스키마 모델은 `extra="forbid"`. 가격은 양수만, `target_weight` 는 0~0.25. 쓰기 허용·전제조건은 `WRITE_RULES`(glob) + 스키마·파일명↔페이로드 일치·`debate_rounds` 상한이 강제한다.
- 원장 쓰기는 호출마다 commit 하고 행 단위로 멱등이다(같은 단계 재적재 = 덮어쓰기, 행 id 유지). 로거는 `trader.spot`.
- `settings.yaml` 키를 더하면 `templates/config/settings.yaml` 도 같이 바꾼다.
- `FAKE_CLAUDE=1` 없이 `trader spot analyze` 를 돌리지 않는다(구독 호출). 운영 메모: `FAKE_CLAUDE=1 trader spot analyze` 를 KIS 키 없이 돌리면 `00_inputs` 에 시세가 없어 `submit-decision` 이 fail-closed 로 거부하고, 체크포인트 11개 + exit 1 로 끝난다(의도된 동작). 키가 있거나 `FakeMarket` 을 주입하면 13개다.

## 테스트

`tests/features/spot/` — 설정·스키마·원장·기능 등록·포트 조립, 리스크 규칙·tradability·summary, 도구·`StageRule`, executor·승인 지문·`exec-key`·`approvals` CLI·텔레그램·잡, FAKE 세션·analyze CLI, 골든 회귀(`test_regression.py`, `stock_regression_v1.json` 로 1회 재기준). 커널 쪽은 `tests/kernel/{session,execution,lane}/`. 포트 구현체 테스트는 `tests/features/accounts/test_ports.py`, `tests/features/ingest/test_ports.py`.

## 커널로 이동한 것 (M10)

- `rule_hit(rule, quote, closes)` 는 커널 `execution/monitor_rules.py` 로 승격됐고 `monitor_rules.py` 는 재노출한다(fo `triggers` 도 쓴다).
- `latest_positions(conn, account_id, market, *, asset="stock")` 는 커널 `execution/ledger.py` 의 asset 필터 판이고 spot 의 자체 구현(`tools._latest_positions`)은 없앴다 — fo 행이 시장 세션 입력에 섞이지 않는다. 시장 세션 입력의 NAV 선택 기준이 최신 NAV 행에서 `MAX(snapshot, nav)` 날짜 행으로 바뀌었다(`account_nav` 에 asset 열 없음).
- `get_trigger`/`mark_trigger_handled` 도 커널 `execution/ledger.py` 로 옮겼고 `ledger.py` 가 재export 한다(fo 재평가 공용).
- FAKE 헬퍼(`fake_call`·`ensure_ok`·`FakeSessionError`·`fake_report`·`fake_turn`)는 커널 `session/fake.py` 를 import 한다. 파생(`fo`) 계좌 분기는 `features/fo`(`fills.sync`·`balance`)가 소유하며 spot 은 현물 계좌만 다룬다.

## 웹 (M11, `routes.py`)

- `approvals_router`: `GET /approvals`·`PATCH /approvals/{id}`(선점 + `approve_execute` 큐).
- 승인 취소 선점 해제(M12): 대기 중 `approve_execute` 를 `DELETE /jobs/{id}` 로 취소하면 `QueueKind.on_cancel` 이 `approvals.release` 로 선점을 푼다. 집행 요청이 중복(`DuplicateRequest`)이라 못 들어가도 선점을 풀고 409.
- 웹 거부 알림(M12): `PATCH {action: reject}` 는 원장 결정 뒤 큐 `approval_notice` 를 넣고 데몬이 텔레그램 `[거부]` 를 보낸다(웹은 알림을 쥐지 않는다). 큐 등록이 실패해도 거부 결과는 그대로(로그만).
- 승인 행 통화(M12): `market`(주문 시장, 없으면 계좌의 유일한 시장, 모르면 null)·`currency`(시장 기준 KR→KRW·US→USD — 원화 계좌로 산 미국 주식도 USD).
- 리스크 한도(M12): `/positions/risk` 는 `limits`(설정 `risk` 의 `max_position_pct`·`max_positions`·`max_sector_pct`·`min_cash_pct`·`daily_loss_stop_pct`)와 `usage`(`position_pct_max`·`positions`·`sector_pct_max`(`ports["scan"]` 섹터, 맵에 없는 종목은 '기타', 포트 없으면 null)·`cash_pct`(브로커 현금/NAV)·`daily_loss_pct`(직전 NAV 대비, 없으면 null))를 `insufficient` 카드에도 싣는다. 게이트와 같이 `excluded_tickers`(헤지·현금 운용 ETF)는 사용량에서 뺀다.
- `positions_router`(조회만): `GET /positions/overview?market=`(현물 계좌 시장·환경별 합산, 파생 제외), `GET /positions/risk?account_id=&market=`(위험 카드 — 종가는 `ports["ml"].close_table`).
- `decisions_router`(조회만, `decisions_view.py`): `GET /decisions/spot/{id}`·`/trace`·`/outcomes`·`/checkpoints/{stage}`·`GET /decisions/spot/news-refs`, `GET /markets/{market}/today`(옛 `/today`·`/market-checkpoint`), `GET /risk-checks?date=`.
- `RESOURCES` 의 `/decisions`·`/orders` 현물 조각(단타 행 포함, `format=csv` 는 merge 가 처리).
- SSE `decisions`·`orders`·`triggers`, nav `today/approvals "승인"`·`today/spot-lane "현물"`.
- 웹 알려진 차이: 스펙 §2.5 의 `/decisions/{id}` 는 자산별 `/decisions/spot/{id}`·`/decisions/fo/{id}` 로 갈렸다. 단타 결정 행은 그날 `origin=scalp` 주문에서 합성하고 id 가 문자열 `scalp-<order_id>` 라 상세(`/decisions/spot/{id}`)는 422(T8-R1, M11b 는 상세 링크 없이 그린다). `decisions` SSE 지문은 현물 결정만 본다 — 파생·단타 행 변화는 `fo`·`orders` 키로 받는다(M11b 는 셋을 함께 구독). `/markets/{m}/today` 에 옛 `discovery_line` 이 없다(scan 소유, 레인이 import 할 수 없음 → M11b 가 `/news/themes` 로 조립, T8-R2). CLI `approvals approve` 는 웹·텔레그램의 decide 를 거치지 않아 `decided_by` 가 비고 notices·control_log 에 남지 않는다(사용자 전용 경로).
- `RESOURCES`: `/accounts/{id}/positions` 현물 조각(`positions_view.spot_rows`). 손절 `{level, source}` 는 모니터와 같은 `stops.effective_stop`, 출처 문구 "결정"·"추적"(보유 묶음 뒤 바닥)·"기본 −N%"; 목표는 결정 `take_profit`("결정"). 헤지·현금 운용 ETF 는 stop 없음.

## 알려진 차이 (옛 저장소 대비)

CLI·명령
- 최상위 `trader analyze`·`trader collect` 별칭이 없다(기능 sub-app 일관): `trader spot analyze`, `trader ingest collect`. 옛 `approvals` 도 `trader spot approvals`. 스펙 M4 행의 `trader analyze --dry-run` 은 `trader spot analyze --dry-run` 으로 읽는다.
- `trader status`(M12): STATUS.md·`/status` 의 spot 절은 「최근 결정」(원장 `decisions` 최근 3건, T7-R1)·「미승인 주문」·「성향」(계좌×시장 `stance.decide`).
- health(M12): 영역 `spot` — `spot.fill_ambiguous`(체결 매칭 모호, warning)·`spot.reconcile.<계좌>.<시장>`(야간 대조 불일치 warning · 현지 평일 예정 시각 + 30분 유예 뒤 미실행 watch — 휴장·건너뜀 제외, KR 휴장은 accounts 포트 `holiday_fn`, 없으면 US 만 `is_us_holiday` 폴백 T4-R1).
- 텔레그램 핸들러는 `CommandContext.ports["accounts"]` 로 계좌·브로커·정책을 받는다(포트가 없으면 안내만, 주문 없음).
- `approvals_expire` 잡은 `boot` 를 부르지 않고 `ctx.conn_factory()`·`ctx.now`·`ctx.notifier` 로 계좌·브로커 없는 최소 `ExecContext` 를 만든다(만료는 conn+now 만 쓴다).
- `approval_line` 은 spot `messages.py` 가 소유한다(커널 `notify.messages` 의 ENV·SIDE·`money`·`kst_hhmm` 재사용).
- 체결 동기화(`fills.py`, `trader spot fills sync`, `fills_sync` 잡·큐 kind): 파생(`fo`) 계좌 분기는 M10 까지 없다(현물 계좌만). 큐 `fills_sync` 본문 `fills_then_balance` 는 체결 동기화 뒤 장 밖이어도 잔고를 바로 다시 읽는다(잔고 실패는 체결 결과를 가리지 않음).
- balance(`balance.py`, `balance_refresh` 잡, `trader spot balance refresh`)·reconcile(`reconcile.py`, `{market}_reconcile` 잡·큐 kind `reconcile`, `trader spot reconcile`): 옛 동작에서 섹터 자동분류(`_classify`)·메모리 브레이커 인자·fo 분기를 뺐다. 킬스위치 관측·미체결 BUY 회수(`cancel_buys`, 데몬만·dry-run 아님·장중)·헤지/cash_park 제외(`excluded_tickers`)는 M6 에서 `ports["regime"]` 로 다시 연결했다(CLI `balance refresh` 는 관측만). 관측이 킬스위치를 새로 켜면 `notifier` 로 포트의 `killswitch_message` 를 1회 알린다(Ruling P11, 데몬·CLI·실행 경로 공통). `snapshot_age` 는 보유 0건 계좌도 신선도가 보이도록 `account_nav` 를 본다. 대사 알림은 (계좌·시장·날짜) 플래그가 이미 있으면 다시 보내지 않는다(하루 1회). 대사 불일치 알림은 종목 표시명이 아니라 티커를 보여준다(옛 `names.display` — 종목명 해석은 M7 `ticker_names` 캐시 이식 때 복원). `reconcile.us` 는 뉴욕 시각(`tz: America/New_York`).

세션·컨텍스트
- 주문 알림 제목 꼬리 표 `_STAGE_KIND`(커널 `execution.core`)는 `order_stage` 끝의 `_rN`(재평가 라운드)·`_entry`·`_exit`(단타 다리) 꼬리를 벗긴 이름으로 찾는다(M9). 그래서 `51_order_r2`(재평가)·`52_order_reprice`(재호가)·`53_order_rebalance`(리밸런스)·`57_order_scalp_entry`(단타) 모두 꼬리가 붙는다. 옛 저장소는 `_r` 로 잘라 찾아 52·53 의 꼬리가 비었다(옛 저장소 대비 알려진 차이).
- `trader_context` 는 quote·risk_limits·costs_bp·portfolio·holding·regime·market_view·quant 를 싣는다(regime·market_view 는 `ports["regime"]` 가 있고 헤지가 켜져 있을 때만, 아니면 None; `quant` 는 M7 부터 `05_quant` 리포트 요약). M8 부터 `memory`·`memory_status`(`ports["evaluation"].memory_context` — 포트·원장이 없으면 `[]`·`{warmup: true, breaker: null}`)와 `calibration`(`35_calibration` 체크포인트, `ml.yaml calibration.enabled ∧ inject` 일 때만, 아니면 None)도 싣는다. theme 컨텍스트는 주입하지 않는다. `35_calibration` 은 `ensure_calibration` 이 `ensure_quant` 와 같은 세 지점(`scan_analyze`·`tool collect`(결과 `calibration`)·`spot analyze`)에서 쓴다.
- `AnalystRole` 은 4종을 유지한다(`ANALYST_ROLES` 와 일치). `05_quant` 는 `QuantReport.role="quant"` 로 별도 스키마다(Ruling M7-K1).
- `resume` 세션·`31_decision_r<n>` 재평가와 옛 `ingest_checkpoint` 의 `stop_floor` 규칙(같은 날 재평가 시 BUY/ADD/HOLD 손절가 최고값 유지)은 M5 에서 이식했다(`resume.py`).
- 에이전트 md 는 `features/spot/agents/` 에 있다(옛 `paths.agents_dir()` 는 M12 에서 삭제).
- `common_rules`(커널 `session.agents`) 는 옛 정규화(끝 마침표 제거)를 복원했다 — 옛 md 간 마침표 위치 차이를 계약 테스트가 허용하기 위함.

장중 감시(`monitor.py`)
- 헤지 ETF·현금 운용(`excluded_tickers`) 보유는 손절·트리거 대상이 아니다(M6) — 결과 `skipped_excluded`(하나라도 있을 때만 키가 생긴다; 옛 결과에는 없던 키). `ports["regime"]` 가 없으면 헤지 ETF 도 일반 보유로 본다. 손절 전 미체결 BUY 취소(`cancel_open_buys`)의 `keep` 기본도 `excluded_tickers` 다(이 기본값 변경이 실질적으로 효과를 내는 곳은 LLM 결정이 매수에서 돌아섰을 때의 BUY 취소뿐이다 — 손절은 이미 제외 종목을 건너뛰고, 킬스위치 회수는 `keep=hedge_tickers`, 현금 운용은 `keep=frozenset()` 을 명시한다).
- 현금 운용(`cash_park.py`, 설정 `cash_park`: `enabled=false`·`ticker=357870`·`reserve_pct=0.20`·`min_amount=500000`, `templates/config/settings.yaml`): 옛 `orchestrator/cash_park` 이식. 세대 전환 때 반대 방향 미체결 취소는 `executor.cancel_open_buys(…, keep=frozenset())` — 기본 `keep`(`excluded_tickers`)이 운용 ETF 자신을 빼 취소가 안 되므로 비운다. auto 계좌만, `dry_run` 은 기록 안 함, 거부·실패는 `MAX_ATTEMPTS`(3)까지.
- 국면 헤지(`hedge.py`): 데몬 `{market}_monitor` 틱의 `hedge_tick`(옛 `_hedge_body`) — 결정 행은 `ledger.insert_hedge_decision`(`source='hedge'`, 라운드 행 없음), 접수 알림 `[헤지 진입]`/`[헤지 해제]`(spot `messages.hedge`). regime 포트·계좌 포트·시세가 없거나 헤지가 꺼져 있으면 `{"skipped": "비활성"}`(`rebalance` 직접 호출은 `hedge.enabled=false`). 옛 `HedgeCfg`·`HedgeInstrument` 를 import 하지 않고 포트의 `hedge_cfg()` 속성만 읽는다(설정에 없는 보유 종목은 내부 `_Inst`). `hedge_tick` 은 오늘 완료 플래그가 있으면(실패 계좌 `pending` 이 남아 있어도) 건너뛴다 — 옛 동작 그대로라 데몬 경로에서는 `MAX_RETRIES` 재시도가 실제로는 일어나지 않는다.
- 헤지 보강: `ports["regime"]` 가 없으면 헤지 주문의 비중 clamp 가 적용되지 않고 `hedge.max_weight_pct` 를 그대로 쓴다(운영 경로에는 항상 포트가 있다). decisions 의 `source` 는 마지막에 쓴 쪽이 정한다 — 헤지 행이 같은 날·종목의 LLM 결정으로 덮이면 `llm`, 반대면 `hedge`(`_upsert_decision`, Ruling P8; 스키마 기본 `llm`). 그래서 같은 행의 `source` 로 헤지 결정을 분포에서 가를 때는 덮어쓰기 가능성을 감안한다.
- `close` 무효화 조건의 종가표는 `MonitorContext.close_table(시장, since, until)` 주입이다(옛 피처 스토어 `ml.dataset.close_table` 은 M8). 주입이 없으면 그 조건은 미판정. 데몬·CLI 는 아직 주입하지 않으며, 그런 결정이 있으면 시장·운영일당 경고 1회를 남긴다(`close_rule_unchecked:<시장>:<날짜>` 플래그).
- US 거래소는 잔고 응답(`Position.exchange`) 다음 `MonitorContext.exchanges` 만 본다. 데몬 `monitor_body`·`trader spot monitor`·재호가는 `ledger.ticker_exchanges(conn, 시장)`(관측한 거래소 표)로 채운다. 옛 `universe.us` 폴백은 없다(유니버스는 ingest 소유).
- 손절 바닥은 `max_stop_since(..., since_date=held_since)` — 보유 묶음 시작일 이후 **날짜**의 결정 라운드 손절가 최고값(옛 `date >= since` 와 같음; 결정 id 순서가 아니라 과거 asof 결정을 나중에 적재해도 바닥에 들어가지 않는다).
- 손절 전 미체결 SELL·BUY 취소와 결정 전환 시 BUY 취소는 `executor.cancel_rows`/`cancel_open_buys` 한 곳을 지나 `kernel.execution.core.cancel` 로 간다(옛 `broker.cancel_order` 직접 호출). `core.cancel` 은 이미 끝난 주문이면 브로커를 부르지 않는다. M9(T6-R1)부터 `core.cancel` 은 잔량(`qty − SUM(fills.qty)`)으로 취소하고, 잔량 0 이면 브로커 호출 없이 `ok False`·"남은 수량 없음(전량 체결)" 이다 — US 부분 체결 주문의 취소 수량이 바뀐다(KR 은 `QTY_ALL_ORD_YN=Y` 라 영향 없음).
- 데몬 `{market}_monitor` 의 게이트(`monitor`)는 일시정지를 보지 않는다(주말·휴장·장중 창만) — 옛 데몬은 `market_job` 게이트가 일시정지에 틱 전체를 건너뛰어 `stop_paused` 알림·트리거 기록이 CLI 에서만 났다. 의도한 개선(Ruling 20): 일시정지 중에도 손절선 도달은 하루 1회 알리고 트리거는 기록하되, 주문·취소·재평가는 `monitor_once` 가 막는다. 주문 경로 잡의 `market` 게이트는 그대로 일시정지에 건너뛴다.
- 재평가 큐 등록은 데몬의 `enqueue_from_job(deps, "reevaluate", …)` — 등록 실패는 경고만 하고 다음 틱에 다시 본다(인라인으로 돌리지 않음). CLI `trader spot monitor` 는 큐가 없어 재평가 세션을 인라인으로 돌린다.

실행·승인
- 승인 실행의 `pending → approved` 조건부 UPDATE 는 `expires_at > now` 도 요구한다 — 재판정(`recheck`) 도중 만료되면 주문 없이 `expired`(옛 저장소는 실행 시작 때만 만료를 봤다).
- 계좌 포트는 현물 계좌만 내놓고 정책은 `account_mode`(실전 파생 항상 off)다.
- 승인 재판정의 킬스위치·bear 거부 문구는 게이트와 같은 꼴(`킬스위치(신규 매수 중단): …`·`국면 bear: 신규 매수 중단`)이다(옛 `킬스위치 켜짐(…) — 신규 매수 중단`·`하락 국면(bear) 전환 — 신규 매수 금지`). `ports["regime"]` 가 없으면 이 둘은 보지 않는다. 승인 계좌가 설정에 없으면 `expired` 로 처리한다. (트리거 가격 재검사는 M5 에서 `resume` 이 맡는다).
- `submit-decision` 결과(재개 `resumed_execution` 포함)에 `memory_entry_id` 가 있다(M8, `ports["evaluation"].record_decision` — 포트가 없으면 None). 엔트리는 라운드별(아침 0, 재평가 r<n>)이고 `decided_at` 은 실행 직전 시각이다. 재개 경로는 `skipped_debate` 를 넘기지 않는다(옛과 같음).
- `approvals.fingerprint` 는 NOT NULL 이라 NULL 지문 승인은 만들 수 없다(옛 `test_approve_legacy_null_fingerprint_passes` → `test_approval_without_fingerprint_cannot_be_created`). 옛 pending 승인의 지문 불일치는 옛 데이터를 옮기지 않으므로 해당 없음.
- 메모리 서킷브레이커·RL 사이저는 M8 부터 `ports["evaluation"]` 으로 받는다: 브레이커는 `build_portfolio` 가 모의 계좌·스냅샷 기록일 때 **그 계좌** 최근 30일 NAV(`nav_history`)로 갱신한다(옛 `_paper_nav_series` 의 모의 계좌 합산·오늘 NAV 덧붙이기는 하지 않는다 — 스냅샷 뒤라 오늘 NAV 는 이미 들어 있다). RL 사이저는 모의 계좌·BUY/ADD·dry-run 아님·origin 이 stop/hedge/park/scalp 아님·결정 행 있음일 때만 제안하고(`apply` 모드면 게이트가 제안 비중으로 판정), `rl_shadow` 기록은 최종 판정 뒤다. 섹터 자동분류(M11)는 아직 없다. 국면·전략가 기울기·킬스위치(관측·당일 미체결 BUY 회수)·권리 일정은 `ports["regime"]`, 테마는 `ports["scan"]`로 받는다. 킬스위치 회수의 US 거래소는 옛 `universe.us` 대신 관측 표(`ledger.exchange_of`)다. stop/origin 5건·트리거 가격·`resume` 재평가는 M5 에서 들어왔다.

M5 운영 경로 (monitor·resume·fills·balance·reconcile·reprice·notice·큐 reevaluate)
- 미이식(범위): 섹터 자동분류(`_classify`, M11 분류), 파생(`fo`) 계좌 분기 전부(M10: `test_balance_dispatch`·`test_fills_fo`). 공시·뉴스 **트리거 생산**(`_news_trigger`·`_disclosure_trigger`)은 M7 — M5 는 이미 기록된 트리거를 소비·재평가만 한다. `ops_check` 잡·health 는 M12 에서 커널 `kernel/ops` + spot 영역으로 들어왔다. 큐 kind 는 `reevaluate`·`fills_sync`·`reconcile`·`rebalance`(M6) 4종이며(`news_collect` 는 ingest 의 interval 잡), `analyze_ticker`·discover 등은 M7 이다.
- `notice` 는 KIS 체결통보(웹소켓) 리스너이지 공시 알림이 아니다. `fill_notice.enabled` 기본값이 True→False 로 바뀌었다. 리스너는 (env, HTS ID) 가 아니라 **env 별 1개**다 — 같은 env 에 HTS ID 가 여럿이면 첫 계좌 것만 구독하고 경고한다. `interval_s=30` 은 코드 고정(`schedule_key=None`). `websockets` 는 `uvicorn[standard]` 전이 의존이다(직접 선언 없음, 옛과 같음). 실전 키는 시세 허브와 다른 앱키를 먼저 고른다(Ruling 18). 같은 키뿐이면 공유 표시만 하고, 웹 시세 허브의 `hub_blocked` 비켜서기(live_quotes yielding)는 M11 이다.
- `reevaluate` 큐 kind 는 `restart_requeue=False` 라 데몬 재기동 때 다시 등록하지 않는다(끊긴 요청은 알림만; 중복 주문 방지). `fills_sync`·`reconcile` 만 재등록한다. 세션 기동 예외 시 트리거를 `실패: …` 로 마감하고 `reevaluation_failed` 알림을 보낸 뒤 예외를 다시 올린다(옛 큐 경로는 예외 전파 → 다음 틱 재등록; Ruling 19). 장 마감 skip(`session_over`)은 사람 요청(web/telegram)에도 적용되고 KR 휴장일은 모른다(daemon 출처는 `auto_gate` 시장 게이트가 막는다). 요청에 `dry_run` 이 없어 데몬 `--dry-run` 을 요청이 뒤집지 못한다.
- fills: `trader spot fills sync` 출력은 계좌마다 JSON 한 줄이다(옛 표 형식 아님). `AUTO_SOURCES` 에 `"restart"` 가 포함된다(Ruling 9). 조회 실패 계좌·시장은 잡 결과의 최상위 `error` 로 올라온다.
- reconcile: 알림이 라벨이 아닌 티커 표기, `snapshot_age`·nav/cash 는 `account_nav` 기준, `reconcile.us` 는 `{time, tz}` 형태이고 `reconcile.kr` 은 문자열도 허용한다(Ruling 11).
- rebalance(`rebalance.py`): 옛 `_scalp_open` 제외는 M9 부터 `ports["scalp"].open_tickers(state, market, today)`(오늘 단타 `open` 종목, 판정 대기 제외) 를 제외 집합에 합친다(포트가 없으면 빈 집합). 레그 포트폴리오에 `sector_of` 를 주입하지 않아 섹터 상한은 리밸런스 레그에서 평가되지 않는다(섹터 맵은 M7 유니버스). "오늘 이미 주문한 종목" 건너뛰기는 옛 의미대로 체결 완료 주문도 센다(`ledger.has_open_order_today`, 취소·거부 등 죽은 상태만 제외; Ruling P9 — 커널 `open_orders_today` 는 미체결만 세므로 쓰지 않는다). 잔고·시세가 전부 실패해도 완료 플래그가 기록된다(옛 동일). US 거래소는 `ledger.ticker_exchanges`(관측 표), 없으면 `NAS`. 큐 kind `rebalance` 는 `restart_requeue=False`(끊긴 요청 재등록 안 함, 중복 주문 방지).
- resume: `_ingest_run` 이 sorted 순서로 적재해 `31_decision_r10` 이 `r2` 보다 먼저 온다 → `already` 경로에서 라운드가 10개 이상이면 decisions 행이 최신이 아닐 수 있다(옛 결함 그대로). `handled_marker` 실패는 `"실패: <error> (<session_id>)"` 문자열이다.
- monitor: 종목별 오류 격리: 시세·보유 조회는 `KisApiError`·`ValueError` 를 경고로, 한 종목 점검의 그 밖 예외는 `logger.exception` + 결과 `errors` 에 적고 다음 종목으로 넘어간다(옛은 틱 전체가 멈췄다 — 의도한 개선). US `Position.exchange` 는 KIS 잔고 응답에 의존한다. 데몬 `{market}_monitor` 주기는 per_market `schedule_key` 의 `{market}` 포맷(Ruling 15, 커널 수정)으로 `kr/us.monitor.interval_s` 가 실제 적용된다.
- reprice: `run_once` 반환은 `{repriced, skipped}`(Ruling 17). "개장" 기준은 `monitor.start`(옛 동일). US 거래소 코드는 `ledger.ticker_exchanges` 에서 읽는다. 이미 재호가로 다시 낸 주문(origin `reprice`)은 후보가 아니다 — 틱 중간 예외로 완료 플래그가 안 서도 재제출분을 다시 쫓지 않는다.

M7 scan_analyze (`scan_analyze.py`, `05_quant`)
- `{market}_scan_analyze` 잡(cron `schedule.yaml scan_analyze.{kr,us}` 기본 07:30 KST / 08:30 뉴욕, 게이트 market)·큐 kind `scan_analyze`·`analyze_ticker`(종목 락 `locks.lock_for` 를 재평가와 공유). 스캔은 `ports["scan"].run_scan`, 분석 세션은 spot 이 돈다. 훅 자리 `scan_analyze.pre`·`scan_analyze.post`(후자는 섀도 기록용으로 비어 있음).
- `scan_analyze` 는 regime 단계(국면 갱신·시장 세션·재판정)를 품지 않는다. 그것은 M6 `{market}_regime` 잡(07:25 / 08:25 ET, 5분 앞, Ruling M7-S1)이 먼저 돈다. 옛 `_scan_analyze_body` 의 regime/market_view 호출이 사라진 이유다.
- `ensure_calibration` 은 M8 에서 `_ensure_quant` 의 `ensure_quant` 바로 뒤에 붙였다. 섀도 포트폴리오 기록·reflect 스냅샷 `snapshot_fn` 은 M8 evaluation 쪽이다(옛 `test_scan_analyze_body_records_shadow_portfolios` 2건 미이식).
- `ScanAnalyzeSchedule` 은 `AtTime` 대신 spot `ReconcileAt` 을 쓴다(레인이 다른 기능의 타입을 import 못 함; 필드 동일).
- `analyze_ticker` 의 `dry_run` 은 `deps.dry_run or 요청값` 이다 — 큐 요청이 데몬 `--dry-run` 을 뒤집지 못한다(우선순위는 데몬 설정).
- `sector_of` 주입 회수: 리밸런스 레그와 executor 가 `ports["scan"].sector_of` 로 섹터 상한을 평가한다(M5 의 "섹터 상한 미평가"가 M7 에서 해소). 리밸런스는 계좌 루프마다 `executor.sector_of_for(ctx.ports, market)` 를 다시 부른다(시장 단위 값이라 낭비, 정확성 영향 없음).
- `05_quant`(`tools.ensure_quant`)는 `ports["ml"]`(`quant_enabled`·`latest_model(market)`·`latest_row`)로 만든다; 포트·승격 모델·피처 행이 없거나 예외가 나면 None(경고 후 건너뜀, 분석을 막지 않는다).

- M8 이후: 메모리·보정(`35_calibration`)·`memory_entry_id`·메모리 브레이커·RL 사이저는 `ports["evaluation"]` 로만 쓴다. `_rl_record` 는 exec-key 선점 뒤에 불려 선점을 잃으면 `applied=0` 으로 남는다. 브레이커 NAV 는 계좌 자신의 30일 시계열이다.
