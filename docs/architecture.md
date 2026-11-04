# 커널 아키텍처 메모

`src/trader/kernel/` 각 모듈의 책임·공개면(이름만)·규약·테스트 위치. 시그니처와 기본값은 코드가 정본이다. 전체 설계는 스펙(`docs/superpowers/specs/2026-11-02-kis-trading-agent-replatform-design.md`) §2.

## platform

- 책임: OS 분기가 허용되는 유일한 계층. 경로·파일락·프로세스·로깅·번들 판별.
- 공개면: `paths`(`project_root`·`config_dir`·`state_dir`·`secrets_path`·`docs_dir`·`web_dist`·`agents_dir`), `bundle`(`is_frozen`·`bundle_root`·`resource_dir`·`templates_dir`·`tool_command`·`cli_argv`), `procs`(`pid_alive`·`same_process`·`spawn_detached`·`stop_tree`), `filelock`(`locked`·`try_hold`), `logging_setup`(`configure`·`file_handler`·`quiet_httpx`).
- 규약:
  - 루트 탐색은 `TRADER_HOME`(절대경로로 resolve) → 없으면 cwd 에서 위로 `pyproject.toml`. 못 찾으면 `ProjectRootNotFound`.
  - `templates_dir()` 는 `bundle` 에만 둔다(`paths` 에 두지 않는다). 소스 실행 시 저장소 `templates/` 를 가리키므로 `TRADER_HOME` 이 저장소 밖이어도 `init` 이 동작한다.
  - 로그 파일은 `trader.log` / `trader-<name>.log`, 로거는 `trader.platform`. 회전 핸들러는 프로세스 간 공유 가능.
  - OS 분기(`sys.platform`)는 이 모듈 안에서만.
- 테스트: `tests/kernel/platform/`

## config

- 책임: yaml 설정 로더와 섹션 레지스트리. 커널 자체 섹션(`settings.markets`·`settings.web`)만 여기서 등록하고, 기능 섹션은 부트가 더한다.
- 공개면: `LlmSettings`(`settings.llm`: 세션 예산·타임아웃·`timeout_for(kind)`·감속 `UsageThrottle`·`UsageProbe`)·`models`·`claude_bin`·`anonymize` 섹션, `load`·`memo_load`, `Section`·`SectionRegistry`·`ConfigFile`, `kernel_registry`·`KERNEL_SECTIONS`, `ConfigError`, `KST`, `Market`, `WebSettings`, `NotifySettings`, `secrets`(`load_secrets`·`Secrets`).
- 규약:
  - `Section.model` 은 pydantic **필드 주석**이다(`list[Market]` 같은 타입도 허용). RootModel `.root` 를 강요하지 않는다.
  - 모든 섹션 모델은 `extra="forbid"`. 알 수 없는 키는 `ConfigError`.
  - `kernel_registry()` 는 호출마다 커널 섹션만 담은 새 레지스트리를 준다. 부트가 복제본에 `feature.sections` 를 등록한다.
  - `KST` 는 `config/time.py` 하나. 다른 곳에서 `ZoneInfo("Asia/Seoul")` 를 만들지 않는다.
  - `templates/config/*.yaml` 은 스키마를 통과해야 한다(자리표시자만).
  - `config.market_hours`: 시장 달력·장중 판정(`REGULAR` 정규장, `in_window`, `us_holidays`/`us_early_closes`(NYSE 휴장·조기폐장 13:00 ET 규칙), `is_us_holiday`, `has_session`, `is_open`, `session_over`, `completed_candles`, `close_hm`/`market_close`). KR 휴장일은 모르고 `holiday_fn`(accounts 포트가 KIS 휴장 조회로 공급)을 받는 쪽이 본다. 옛 시그니처를 그대로 둔다.
- 테스트: `tests/kernel/config/`

## ledger

- 책임: SQLite 연결(WAL, 쓰기 타임아웃 30초), 버전 마이그레이션, 커널 소유 원시(`control` 플래그·`runs`).
- 공개면: `connect`·`init_schema`·`current_version`·`now_iso`, `Migration`·`KERNEL`·`check`, `control`(`get_flag`·`set_flag`·`delete_flag`), `runs`(`start`·`finish`), `LedgerError`.
- 규약:
  - 기본 DB 파일은 `state/trader.db`.
  - 마이그레이션은 전역 버전 오름차순·추가만. `migrations.check` 는 위반 시 `LedgerError` 를 던진다(레지스트리 쪽 `contracts.check` 가 이를 메시지 목록으로 바꾼다).
  - `init_schema` 는 이미 최신이면 쓰기 잠금 없이 반환한다.
  - 저장 시각은 `now_iso()` 의 UTC ISO8601.
  - 커널은 `control`·`runs` 외의 도메인 테이블을 모른다. 기능이 자기 마이그레이션으로 만든다.
  - M7 에서 종목 표기 `ledger/names.py`(`ticker_names` → 캐시 JSON, 네트워크 없음)가 기능에서 커널로 승격됐다.
- 테스트: `tests/kernel/ledger/`

## session

- 책임: LLM 세션 공용 몫. 원장 v4(세션 행·구독 사용률), 에이전트 정의 로더·프롬프트 조립, 익명화, 단계 전제조건 검사, 체크포인트 저장소, `claude -p` 실행. 기능 이름을 모르며, 세션 종류별 차이는 `SessionKind`·`AgentDef`·`StageRule` 선언이 정한다.
- 모듈:
  - `migrations`: 원장 v4 `SESSION` — `agent_calls`(세션 1회 = 1행)·`llm_rate_limits`(구독 사용률 관측).
  - `calls`: `start_call`(일일 상한 검사 포함)·`finish_call`, 오늘(KST) 세션 수 집계 `sessions_today`·`calls_today`.
  - `usage`: 구독 한도(5시간·7일) 관측 두 곳(세션 stream-json 의 `rate_limit_event` → `llm_rate_limits`, 대화형 상태줄 `llm.usage_file`) 중 최신을 `latest_observation`, 사용률이 높으면 새 세션을 줄이는 `session_allowance`·`allowance`·`throttle_enabled`.
  - `agents`: 에이전트 md(YAML 프런트매터 + 본문) 로더 `load_agent`·`by_name`, `--agents` JSON `agents_json`, 오케스트레이터 프롬프트 `orchestrator_prompt`, 공통 규칙 `common_rules`. 모델은 `models` 설정이 md 기본보다 우선하고, md 의 `python -m trader tool` 은 `bundle.tool_command()` 로 치환한다.
  - `schemas`·`debate`·`tooling`(M6 승격): 세션 공용 결정/토론 스키마, 토론 요약 `research_summary`, 도구 헬퍼(`load_turns`·`shrink`·`call_meta`·`trader_summary`). spot 과 regime 이 함께 쓴다.
  - `anonymize`: 프롬프트 직전 종목명·티커 익명화(`anonymize`·`deanonymize`, `Alias`). 문자열 값만, 단어 경계에서만 치환한다.
  - `stages`: `StageRule`(허용 단계 glob + 선행 단계 + 최소 개수) 검사 `first_rule`·`check_rule`.
  - `run_store`: 체크포인트 저장소 `state/runs/<운영일>/<시장>/<종목>/<이름>.json` — `run_date_for`·`run_dir`·`market_dir`, `checkpoint_payload`·`write_checkpoint`·`read_checkpoint`·`list_checkpoints`.
  - `runner`: `run_session(spec, kind, ctx, …)` (`kind.retry_guard` 면 같은 운영일 같은 종목 세션 타임아웃이 `llm.timeout_retry_max` 번이면 다시 띄우지 않음) — `SessionSpec`·`SessionResult`·`PipelineError`·`ThrottleBlocked`·`SessionCancelled`, `build_command`·`parse_stream`·`run_claude`·`terminate_group`. 세션 1회 = `agent_calls` 1행 + `runs` 1행. 서브에이전트 정의는 `--agents <json 파일>`, 도구는 `--allowedTools "Bash(<tool_command> *)"` 만, cwd 는 빈 임시 디렉터리, 세션 PATH 앞에 `bundle.tool_bin_dir()`. `FAKE_CLAUDE=1`(`is_fake`)이면 subprocess 없이 `SessionKind.fake(ctx, spec)` 가 체크포인트를 쓴다.
- 공개면: `kernel.session.__all__` — `SESSION`·`SessionSpec`·`SessionResult`·`PipelineError`·`run_session`·`by_name`·`check_rule`·`first_rule`·`run_dir`·`run_date_for`·`checkpoint_payload`·`write_checkpoint`·`read_checkpoint`·`list_checkpoints`. 나머지는 서브모듈 경로로 import 한다.
- 규약:
  - 운영일 `run_date_for(market, now)`: KR 은 KST 날짜. US 는 현지 날짜의 개장 시각(09:30 ET)을 KST 로 바꾼 날짜라서, 개장 전 analyze 와 KST 자정 뒤 장중 monitor·재평가가 같은 디렉터리를 본다.
  - `run_dir(d, market, ticker, state=None)` 은 종목 디렉터리를 만들어 돌려주고 `state/runs` 밖을 가리키면 `ValueError`. `market_dir(..., create=False)` 은 시장 단위 산출물(`_scan`·`_screen`·`_reflect`) 자리.
  - 체크포인트 봉투 `checkpoint_payload(data, call=None)` = `{schema_version, data, call}`(`CHECKPOINT_SCHEMA_VERSION`=1). `write_checkpoint` 는 pid·스레드 접미사 임시파일에 쓴 뒤 `atomic_replace` 로 교체한다. `read_checkpoint` 는 없으면 `None`, 이름이 디렉터리 밖을 가리키면 `ValueError`.
  - 일일 세션 상한 초과는 `PipelineError`. 모든 단계 파일은 `run_store` 함수로만 읽고 쓴다.
  - 마이그레이션 `SESSION` 은 `ledger.migrations.KERNEL` 이 아니라 `contracts.KERNEL_MODULES` 로 합쳐진다(`KERNEL` 만 쓰는 커널 테스트가 깨지지 않게).
  - M10: `tooling`(도구 공용 `schema_error`·`validate_payload`·`summarize_turns`, regime·fo 가 공유)·`fake`(`fake_call`·`ensure_ok`·`FakeSessionError`·`fake_report`·`fake_turn` — spot·regime·scan·evaluation·fo 가 공유). fo 의 세션 종류 `fo`·`fo_resume`(`SessionKind`, `extra_cap_key=None`)는 일일 상한을 커널이 모르는 설정(`fo.session_cap`)에 두기 위해 fo `session.run` 이 세션 앞에서 오늘 `runs.kind='fo'` 건수를 검사한다(원자적 예약 없음). `usage.RESUME_KINDS` 에 `fo_resume` 이 들어 80% 감속에서도 보유 재평가는 통과한다. 세션 상한 초과는 fo 에서만 `session.run` 밖으로 `PipelineError` 가 나가고 잡·큐 본문이 `skipped` 로 바꾼다.
- 테스트: `tests/kernel/session/`

## execution

- 책임: 주문 실행 코어. 모든 주문이 지나는 한 곳 — 실행 키 선점(멱등)·브로커 전송·주문/승인 원장 기록·사람 승인 관문. 기능 이름과 `features` 를 모른다. 알림·로그에는 계좌 id 만 쓴다(계좌번호 금지).
- 모듈:
  - `migrations`: 원장 v5 `EXECUTION` — 주문·승인·리스크 판정·`exec_locks`·`buy_reservations`·포지션 스냅샷·계좌 NAV·`fills`. `approvals.fingerprint` 는 NOT NULL.
  - `types`: 주문 의도 `OrderIntent`·실행 정책 `ExecPolicy`·게이트 계획 `GatePlan`·`OrderBookkeepingError`, 상수(`ORIGIN_CHECK`·`APPROVAL_TTL`·`EXEC_KEY_STALE`), `dead_status_sql`. `broker.models`·`config`·표준 라이브러리만 import 한다.
  - `idem`: 실행 키 저장소 `IdemStore`·`ExecKeyStore`. `exec_locks` 가 키를 쥐고 매수 예약이면 `buy_reservations` 가 계좌·시장·종목·금액을 남겨 동시 판정이 서로의 진행 중 예약을 보게 한다.
  - `ledger`: 원장 원시 — 주문·승인·리스크 판정·스냅샷·NAV 기록과 게이트 입력용 조회(`realized_pnl_5d`·`held_since` 등). 옛 `ledger/repo.py` 와 같은 시그니처·공식(게이트 입력이라 공식이 바뀌면 매매가 바뀐다). 금액은 Decimal ↔ TEXT, 시각은 UTC ISO.
  - `core`: `transmit`(보낸 시각 `mark_sent` → 브로커 → 상태 결정 → 원장 → 키를 주문에 묶거나 놓음 → 체크포인트·알림), `request_approval`·`approve`·`reject`·`expire_approvals`, `approval_fingerprint`, `ExecDeps`. `approve` 는 기능이 아는 판단(계좌 정책·결정 방향·재판정·의견)을 콜백으로 받고, `recheck` 는 `None` 통과 / `str` 사유 → expired / `KeepPending(reason)` → pending 유지.
  - `core.cancel(deps, *, order_id, order_no, market, broker, dry_run, reason, exchange=None)`: 미체결 주문 1건 취소의 유일한 경로. US 취소에 필요한 `org_no`·`ticker`·`qty` 는 주문 행에서 읽는다. 이미 끝난 주문(`DEAD_STATUSES`)은 브로커를 부르지 않고 `ok=False`. 브로커 취소 1회, 접수되면 `cancelled` 로 바꾸고 거부(`ok=False`)는 상태를 두며, 예외는 상태를 그대로 두고 다시 올린다. `dry_run` 은 브로커·상태 모두 건드리지 않는다. 기능은 `broker.cancel_order` 를 직접 부르지 않는다 — spot 의 미체결 취소(결정 전환 시 BUY 취소, monitor 의 손절 전 SELL·BUY 정리)는 `executor.cancel_rows`/`cancel_open_buys` 한 곳을 지나 `core.cancel` 로 가고, reprice 도 `core.cancel` 을 부른다. `tests/kernel/registry/test_order_paths.py` 가 `features/` 아래 `cancel_order(`·`place_order(` 호출을 막는다. M9(T6-R1)부터 취소 수량은 잔량(`qty − SUM(fills.qty)`)이고, 잔량 0 이면 브로커 호출 없이 `ok False`("남은 수량 없음(전량 체결)").
  - `ports`: 코어가 기능에 기대는 접점 Protocol `AccountsPort`·`InputsPort`. 구현은 accounts·ingest 기능이 맡는다.
  - M9: `StockExecPort`(현물 집행 서비스 — `execute`·`cancel`·`sync_fills`, 구현은 spot `ports["stock_exec"]`: 레인이 다른 레인에 포트를 공급하는 첫 선례)·`ScalpPort.open_tickers`(scalp 제공)·`ScanPort.watchlist`, `types.OPEN_STATUSES`. 주문 알림 꼬리 표 `_kind` 는 `order_stage` 의 `_rN`·`_entry`·`_exit` 꼬리를 벗겨 찾는다.
  - M10: 파생(fo) 레인은 `core.transmit` 을 쓰지 않고 **자체 실행기**(`features/fo/executor.py`)로 낸다 — 다리 상태 갱신과 `orders` INSERT 가 한 트랜잭션이어야 하고(`insert_order(commit=False)`), 요청 타입(`FoOrderRequest`)·명목 금액(승수)·알림 문구가 현물과 달라서다. 멱등은 `LegStore(IdemStore)`(키 `leg:<id>`, `fo_order_legs` 가 정본). 이 예외도 "KIS 주문 API 는 한 모듈만 부른다" 를 지킨다(fo 에서는 executor 와 사용자 전용 `order_test` 만 `place_order` 를 부른다). 커널에서 재사용: `OrderBookkeepingError`·`ExecPolicy`·`notify_quietly`. 변경: `AccountsPort.fo_accounts()/fo_brokers()` 추가·`brokers()` 반환형 `list`, `ledger.latest_positions(asset=)` 필터, `ledger.insert_fill(commit=)`, `ledger.get_trigger/mark_trigger_handled`(spot 에서 승격), `monitor_rules.rule_hit`(spot 에서 승격).
- 공개면: `kernel.execution.__all__` — `EXECUTION`·`OrderIntent`·`ExecPolicy`·`GatePlan`·`OrderBookkeepingError`·`ExecKeyStore`·`IdemStore`·`KeepPending`·`approval_fingerprint`·`AccountsPort`·`InputsPort`. 원장 원시는 `from trader.kernel.execution import ledger as xledger`, 코어는 `core` 모듈로 쓴다.
- 규약:
  - 주문 전송은 `core.transmit` 만 한다. 기능이 KIS 주문 API 를 직접 부르지 않는다.
  - 보낸 뒤(`sent_at`) 주문 행 기록 전에 끊긴 실행 키는 영구 선점이다(같은 결정·계좌·방향 주문이 다시 안 나간다). 사람이 KIS 주문 유무를 확인한 뒤 `ExecKeyStore` 로 푼다.
  - `take_pending` 이 pending 이 아닌 승인은 실제 상태를 돌려주고 DB 를 바꾸지 않는다(approved 행을 expired 로 덮지 않는다). 승인 지문은 항상 비교한다.
  - 마이그레이션 `EXECUTION` 은 `session` 과 같은 방식(`contracts.KERNEL_MODULES`)으로 합쳐진다.
  - M7 에서 보유기간 프로필 `execution/horizon.py`(결정 `horizon_days` → short|mid|long 의 점검 주기·임계, 순수 함수·덕 타이핑)가 spot 에서 커널로 승격됐다(scan 트리거와 spot 이 함께 쓴다).
- 테스트: `tests/kernel/execution/`

## lane

- 책임: 현물·파생·단타 레인이 공유하는 계약 타입. 커널에는 Protocol·DTO 만 두고 구현은 각 레인 기능이 한다.
- 모듈: `types` — `WorkItem`(collect 가 돌려주는 처리 단위: 종목·거래소)·`LaneContext`(설정·conn·state·now·dry_run·notifier·`ports`·`ext`)·`Lane` Protocol(`schedule`·`allowed`·`collect`·`decide`·`plan`·`tick`·`reevaluate`·`reflect`), 상수 `LANES`(`spot`·`fo`·`scalp`).
- 규약:
  - 레인 기능은 어떤 기능도 import 하지 못한다. 필요한 서비스(계좌·수집)는 `LaneContext.ports` 로 주입받는다(`Feature.ports` 가 `boot(ports=True)` 에서 조립됨).
  - 레인 고유 값은 `LaneContext.ext` 에 둔다.
  - M10 fo 레인이 더하는 것: 데몬 잡 `kr_fo_session`(08:20)·`kr_fo_monitor`(60s)·`kr_fo_reconcile`(전부 KR·`gate="none"`, 본문이 파생 시계를 직접 본다), 큐 kind `fo_session`·`fo_reevaluate`·`fo_close`·`fo_reconcile`, control 플래그 접두 `fo_verified`·`fo_halted`·`fo_day_open`·`fo_alerts`·`fo_ref_iv`·`fo_pending_entry`·`fo_replace`(`Feature.control_namespaces`; 별도 `fo_flags` 테이블은 두지 않는다). `ports["accounts"].fo_accounts()` 가 파생 계좌를 주고 `ports["regime"]` 가 국면·킬스위치·시장 뷰를 준다.
- 테스트: `tests/kernel/lane/`

## broker

- 책임: KIS Open API 접근 전체. 인증·HTTP 클라이언트(레이트·재시도)·시세·주문/잔고·파생·웹소켓 프레임·가짜 구현·점검(`ping`)·클라이언트 조립. 주문 판단은 모른다.
- 공개면: `errors`(`KisApiError`·`UNKNOWN_RESPONSE_CODE`), `models`(DTO 전부, `BrokerAccount`·`FoProduct` 포함), `creds`(`KisCreds`·`key_fingerprint`), `auth`(`KisAuth`), `client`(`KisClient`·`RateLimiter`·`RateLimitBusy`), `rate_store`(`SharedRateLimiter`), `market`(`KisMarket`)·`market_fo`(`FoMarketMixin`), `broker`(`KisBroker`), `fo`(`KisFoBroker`)·`fo_master`·`fo_codes`, `ws`(프레임 파서·구독 메시지), `fake`(`FakeMarket`·`FakeBroker`·`FakeFoBroker`), `factory`(`client_for`·`build_market`·`build_broker`·`shared_state_dir`·`reset_clients`), `ping`(`run_checks`·`failures`·`Check`), `tr_ids`, `ticks`(`quantize_price` 등), `_parse`(`dec`·`to_int`·`rows`).
- 규약:
  - **KIS 실패는 전부 `KisApiError(msg_cd, msg1, tr_id)`.** KIS 응답이 아닌 실패는 합성 코드 `AUTH/NETWORK/NETWORK_UNKNOWN/HTTP<status>/INVALID_JSON/UNSUPPORTED`. `NETWORK` 는 보내지도 못함, `NETWORK_UNKNOWN` 은 보낸 뒤 응답 없음(주문이 살아 있을 수 있어 호출자가 구분한다). `rt_cd≠0` 도 `KisApiError`.
  - **시세·수급은 실전 env 클라이언트만, 주문·잔고는 계좌의 env.** 모의 서버는 시세류를 주지 않는다. `tr_ids.py` 가 tr_id·경로의 유일한 출처이고 모의 미지원 API 는 paper 값이 None. 세 겹: `KisAuth(env)` → `KisClient(env, auth)` → `KisMarket(client)`(실전만) / `KisBroker(account, client)`(env 일치 요구).
  - **브로커 주문.** KR 은 `order-cash`(지정가 00·시장가 01), US 는 `order`(지정가만, 거래소 코드는 시세 EXCD 와 다른 `NASD|NYSE|AMEX`), 취소는 `order-rvsecncl`(KR 은 접수 응답의 `KRX_FWDG_ORD_ORGNO` 가 `OrderResult.org_no` 로 필요). `FakeBroker` 는 `orders`·`cancelled` 에 기록한다.
  - **계좌별 키.** 앱키는 계좌마다 발급되므로 계좌마다 키 묶음(`KisCreds`)을 고른다. `client_for` 가 앱키마다 `KisClient` 하나를 캐시한다(캐시 키 `(env, app_key)`). 토큰 캐시·레이트 버킷은 앱키 단위(`KisCreds.bucket` = `<env>-<앱키 지문 12자>`)라 키끼리 덮어쓰거나 한도를 나눠 먹지 않는다. 캐시 파일에 앱키 지문을 남겨 다른 키의 토큰은 읽지 않는다(지문 없는 옛 파일은 사용).
  - `build_broker` 가 `asset` 으로 `KisBroker`/`KisFoBroker` 를 가른다(fo 는 `fo_product` 필수).
  - 토큰: 만료 1시간 전 갱신, 실패 후 60초 재발급 금지, 접속키 20시간. 실패 기록은 다시 읽은 파일에 덧붙여 다른 프로세스가 막 쓴 토큰을 지우지 않는다.
  - 레이트: `RATE_PER_SEC = {"real": 20, "paper": 2}`. `EGW00201`(초당 한도)은 HTTP 200 뿐 아니라 HTTP 500 본문으로도 오므로 상태코드와 무관하게 0.5·1.0·2.0초로 최대 3회 재시도한다. 교차 프로세스 한도는 `kis_rate.db`(`SharedRateLimiter`).
  - US 예수금은 `inquire-present-balance`(통화별 행). 일봉·수급 목록은 항상 날짜 오름차순(마지막이 최신). 금액은 `Decimal`. `OrderRequest.exchange` 는 기본값이 없어 KR 도 `None` 을 명시한다.
  - `TRADER_KIS_SHARED_DIR` 는 토큰 캐시(`kis_token_<env>-<지문12>.json`)·레이트 DB(`kis_rate.db`)만 옮긴다. 휴장일 캐시 `cache/holiday_YYYYMM.json` 은 `state/` 에 남는다.
  - 로거 이름 `trader.broker`. 커널 모듈이라 `features` 를 import 하지 않는다.
- 알려진 차이(옛 저장소 대비, 동작은 보존하고 여기에만 적는다):
  - ① `kis ping` CLI 는 M2 로 미룬다(코드 경로 `ping.run_checks`·`failures` 만 M1).
  - ② `_market_creds`(시세 키 선택)와 같은 키 묶음 경고는 M2.
  - ③ 웹소켓 I/O(연결·재접속·구독 관리)는 M5·M11. M1 은 `ws` 의 순수 프레임 파서·구독 메시지만 둔다.
  - ④ 옛 「미확인」 승계(실기 확인 전까지 추정): US 잔고·예수금은 `OVRS_EXCG_CD=NASD` 고정이라 NYS/AMS 보유분이 같이 조회되는지; 순위 API 3종(`VOLUME_RANK` FHPST01710000·`FLUCT_RANK` FHPST01700000·`FRGN_INST_TOTAL` FHPTJ04400000)의 요청 파라미터와 응답 필드명은 공개 예제 기준 추정(파서는 후보 키를 순서대로 보고 코드가 없으면 행을 버린다); 체결통보(H0STCNI0/H0GSCNI0)를 HTS ID 로 구독하면 같은 명의 계좌 전부의 체결이 어느 앱키 세션으로든 오는지; FO `PRDT_TYPE` 301/302/303 은 추정.
  - ④-실기 2(보유 행이 있어야 확인, 확정 전 해당 수단은 신규 거부): 잔고 output1 의 방향 필드(`sll_buy_dvsn_cd` 유무·값)와 `prdt_type_cd` 값 · `prsm_dpast_amt` 가 NAV 인지 · 체결 응답 `pdno` 가 표준 12자리인지 단축 코드인지 · 취소 행의 필드명(`rvse_cncl_dvsn_cd` 02 / `*_name` '취소') · 미니옵션 승수 5만 · 옵션 매도 `ord_psbl_qty` 의 증거금 반영 · `margin_rate` 의 보수성 · KIS 선물 `basis` 가 시장 베이시스인지 · `hts_rmnn_dynu` 잔존일 단위(달력/영업일) · 만기 정산이 체결 조회에 실리는지 · 감액 다리 재시도 중 손절 우선.
  - ⑤ `KisBroker` 생성자가 `asset != "stock"` 을 `ValueError` 로 거부한다(옛 `_build_brokers` 의 분기를 생성자가 이어받음).
  - ⑥ 로거 이름 `trader.kis` → `trader.broker`.
  - ⑦ 옛 `kis_market.py` 하단 attach 블록 → `market_fo.FoMarketMixin`(본문 동일).
  - ⑧ `rate_store` 는 자체 SQLite 설정(`timeout=5`, autocommit, WAL)을 쓰고 `ledger.connect` 를 쓰지 않는다.
  - ⑨ `BrokerAccount.markets` 기본값은 `["KR"]`(옛 `AccountConfig` 는 `["KR","US"]`) — yaml 의 기본 채움은 M2 설정 모델 몫; 옛 `_build_brokers` 의 "키 없는 계좌는 경고 후 건너뜀"·`ConfigError` 감싸기도 M2(`client_for` 는 `ValueError`).
  - M10: `indicators`(기술 지표 계산 — ingest 에서 승격, ML·spot·fo 가 공유; ingest 는 재노출). 시장 시간의 파생 판은 `config/market_hours`(`is_fo_open`·`fo_close_hm`·`index_live`).
- 테스트: `tests/kernel/broker/` (HTTP 는 respx, fixture JSON 은 합성 응답)

## registry

- 책임: 기능 계약(`Feature`)과 그 타입, 기능 발견, 계약·import 경계 검사.
- 공개면: `Feature`·`JobSpec`·`Hook`·`SseKey`·`HealthArea`·`StatusSection`·`TelegramCommand`·`ToolCommand`·`StageRule`·`OriginSpec`·`QueueKind`·`SessionKind`(세션 종류: 에이전트·프롬프트 절·스키마·타임아웃 키·`runs` kind·상한 키·감속 등급·FAKE·stdin 보충·`retry_guard`(같은 종목 반복 타임아웃 차단 — 커널은 세션 이름이 아니라 이 선언만 본다))·`AgentDef`(에이전트 md 경로 + 출력 스키마)·`StageRule`(`ToolCommand.stage_rules` 의 단계 전제조건)·`OriginSpec`(주문 출처: 리스크·주문 단계 이름·결정 행 유무·게이트 면제·멱등 단위)·`ToolCommand`(`trader tool` 하위 명령, `handler` 가 `Typer` 면 그룹)·`Port`(부트가 `build(boot)` 로 조립하는 이름 붙은 구현체). `Feature` 의 M4 추가 필드: `tools`·`origins`·`session_kinds`·`agents`·`ports`, `RegistryError`, `discover`, `check`, `import_violations`.
- 규약:
  - `discover()` 는 `trader.features` 아래 각 패키지의 `feature.FEATURE` 를 `importlib`+`pkgutil` 로 동적 import 한다. kernel 이 features 를 import 하는 유일한 경로다.
  - import 3층 규칙: `kernel` ← 서비스 기능 ← 레인. 다른 기능은 `api.py` 로만, 레인은 기능 import 금지, 커널은 기능 import 금지(`discover` 제외). `import_violations` 가 AST 로 검사하며 `from pkg import features`·상대 import 형태도 잡는다.
  - `check(features, roots=None)`: `roots` 기본값은 `trader.features.__path__`. 기능 id·잡 id·큐 kind·SSE 키·origin·에이전트·포트 중복, 기능 폴더 README 존재, 마이그레이션 규칙을 메시지 목록으로 돌려준다.
  - 훅: `hooks.by_point(features)` 가 각 `Feature.hooks` 를 포인트별 튜플로 묶고(기능 순서·선언 순서 유지), `hooks.run(hooks, point, **kw)` 가 차례로 부른다. 훅 하나의 예외는 기록하고 다음 훅을 계속하며(실패 격리), 훅마다 `{"hook","result"}` 또는 `{"hook","error"}` 한 건을 돌려준다. 포인트는 `news.post_ingest` 등. 커널은 훅 내용을 모른다.
  - `contracts.KERNEL_MODULES` 가 커널 모듈 마이그레이션(session v4·execution v5)을 `all_migrations` 에 합친다. `check` 는 origin·에이전트 이름·포트 이름 중복, `tier=lane` ↔ `LANES` 교차, `OriginSpec.origin` 이 `orders.origin` CHECK(`ORIGIN_CHECK`) 안인지도 검사한다.
  - 이름이 `features` 인 기능 폴더는 두지 않는다(루트 추론이 `<root>/features` 존재에 의존).
  - M10: 기능 `fo`(레인)가 등록됐다 — `SessionKind` 2·`AgentDef` 2·`ToolCommand` 2(`fo`·`submit-fo-decision`)·`JobSpec` 3·`QueueKind` 4·`TelegramCommand` `/fo`·`StatusSection` 「파생(선물옵션)」·마이그레이션 14. 레인이 다른 기능의 에이전트(애널리스트·리서처)를 재사용하는 길은 `discover()` 로 모은 `Feature.agents` 이름 매핑뿐이다(`fo/session_kinds.agents_for_runner`).
- 테스트: `tests/kernel/registry/` (합성 fixture 는 `tests/fixtures/`)

## daemon

- 책임: 스케줄러 셸. 잠금·하트비트·JobSpec → APScheduler·`run_job`. 기능별 잡 내용은 모른다.
- 공개면: `Daemon`·`DaemonDeps`·`run_job`, `Lock`, `Heartbeat`·`read_status`, `ScheduledJob`·`build`.
- 규약:
  - `JobSpec.schedule_key` 는 `schedule.yaml` 의 **점 경로**이고 `{market}` 자리를 시장 코드로 채운다(`"{market}.scan"` → `KR.scan`). 값은 `"HH:MM"` 또는 `{time, tz}`(tz 기본 KST).
  - 잡 본문 호출은 `body(ctx, job)`. `runs` 행은 `kind=spec.kind`, `market=job.market`.
  - `Daemon.start()` 는 `bool` 을 돌려준다. 잠금을 못 얻으면 `False`. 실패 경로에서는 스케줄러·시그널·잠금을 되돌린다.
  - 하트비트는 30초 주기 스케줄러 잡 `daemon_heartbeat`. `read_status` 가 웹의 `daemon` SSE 키와 상태 표시에 쓰인다.
  - 게이트가 사유를 돌려주면 cron 잡은 `skipped` 행을 남기고 interval 잡은 행을 남기지 않는다.
  - `DaemonDeps` 가 데몬이 받는 의존성 전부다(설정·스케줄·연결 팩토리·상태 폴더·시계·시장·게이트·정지 이벤트·`dry_run`).
  - `DaemonDeps.secrets/registry/hooks` 로 잡 본문이 시크릿·레지스트리·훅을 받는다. 종료(`Daemon.shutdown`) 때 등록된 잡의 `spec.teardown(deps)` 를 spec 당 1회 부른다(실패 격리). interval 잡 주기는 `JobSpec.schedule_key` 가 가리키는 `schedule.yaml` 정수(초)를 우선한다.
  - **게이트**: `JobSpec.gate`(`none`|`market`|`monitor`)는 `Feature.gates`(`GateSpec(name, build)`) 가 공급한다. `build(boot, deps)` 가 `gate(job) -> 사유|None` 을 돌려주고 `build_daemon_deps` 가 `deps.gates[name]` 에 채운다(accounts: `market` = 일시정지·주말·휴장, `monitor` = 주말·휴장 + `<시장>.monitor.start/end` 창 — 일시정지는 보지 않는다(장중 감시가 `stop_paused` 알림·트리거 기록을 하고 주문은 레인이 막는다, M5 Ruling 20), 창 밖 사유는 "창 밖"). 판정 순수 함수는 `daemon.gates.should_skip(market, today, *, paused, is_holiday)`. 커널은 게이트 이름만 알고 브로커·계좌를 모른다.
  - **잡 큐**(`daemon.queue`·`queue_jobs`, 원장 v8 `job_requests`): 기능이 `QueueKind(kind, params_model, label, lane, priority, trading, restart_requeue, body)` 를 선언한다. 요청은 `enqueue`(중복은 `DuplicateRequest`)·`claim_next`·`finish`·`cancel` 로 오가고 `trader jobs list|run|kinds` 와 `enqueue_from_job(deps, kind, params, source=…)` 가 등록한다. 큐 잡 4개 `job_queue`(slow)·`job_queue_fast`·`job_queue_reeval`·`job_queue_info` 는 각자 자기 레인 kind 만 `schedule.yaml job_queue.interval_s`(기본 10초) 주기로 꺼낸다. 상태(`QueueState`)는 `DaemonDeps.queue` 에 둔다.
  - **heavy 락·자동 출처**: `JobSpec.heavy` cron 은 락을 기다리며 `heavy_wanted` 를 세우고, slow 레인 요청은 progress 경계에서 락을 양보한다. `AUTO_SOURCES`(`daemon`·`news`·`notice`·`restart`, 출처의 `:` 앞부분으로 맞춘다 — `notice:paper` 는 `notice`) 요청 중 `trading` kind 는 꺼낼 때 시장 게이트·장 마감을 다시 검사한다(큐에서 오래 기다린 요청이 다음 날 주문을 내지 않게). 사람(cli/web/telegram) 요청은 시장 게이트를 보지 않는다.
  - **재기동 복구·캐치업**: 기동 때 `bootstrap_queue`(이전 running → error "데몬 재시작", 6시간 넘은 queued 만료, `restart_requeue` kind 만 `source="restart"` 로 재등록, 끊긴 요청 알림 1건) → `daemon.bootstrap` 훅 → stale `running` 세션(1시간↑) 정리 → `catch_up_missed`(`JobSpec.catch_up="queue"` 인 놓친 cron 을 큐로 한 번 따라잡기, 조회 범위 20시간). 이 단계는 **잠금을 잡은 뒤에만** 돈다(둘째 데몬이 실행 중 데몬의 행을 닫지 않게). `JobSpec.misfire` 에 따라 grace 가 다르다(`scan` 1시간·`idempotent` 6시간·나머지 2분).
  - **내장 잡**: `daemon.builtin_jobs.BUILTIN_FEATURE`(`kernel_builtin`)의 `telegram_poll`(interval, `schedule.yaml telegram_poll.interval_s` 기본 30초) — outbox 재전송(실패 삼킴) 뒤 `getUpdates` 1회로 명령 처리. 수신 설정이 없으면 `skipped`.
  - **bootstrap 훅·ports**: 훅 지점 `daemon.bootstrap` 은 `deps` 를 받아 잠금·`bootstrap_queue` 직후 한 번 돈다. `DaemonDeps.ports` 는 부트의 `Port` 구현체(`accounts`·`inputs`)이고 잡 본문(spot 포함)은 이것만 통해 계좌·브로커·시세·휴장일·수집에 닿는다(기능 간 import 금지).
  - `JobSpec.days`(cron 요일, 예 `mon-sat`)와 `schedule_key` 가 리스트(`fills_sync.times`)이면 시각마다 cron 트리거를 펼친다. per_market 잡의 id·`schedule_key` 의 `{market}` 은 소문자(`kr_monitor`, `reconcile.kr`)로 치환된다.
- 테스트: `tests/kernel/daemon/`

## web

- 책임: FastAPI 셸. Host·tailnet 게이트·인증 3등급·커널 자원(system·jobs·runs·calls·events)·기능 라우터와 merge 자원 마운트·SSE 지문 조립·`nav.ts`/`sse-keys.ts` 생성·정적 dist. 기능 이름은 모른다.
- 공개면: `create_app`, `WebDeps`(`ports`·`tailscale_users`·`public_base` 포함), `auth`(`require_token`·`require_direct_local`·`require_same_origin`·`is_direct_local`·`require_confirm`·`actor_of`), `merge`(`mount`), `sse`(`snapshot`·`KERNEL_PROBES`), `gen`(`sse_keys_ts`·`nav_ts`), `csv.csv_response`, `routers.KERNEL_ROUTERS`.
- 규약:
  - **게이트 + 인증 3등급**(M11): 미들웨어가 Host 를 루프백·`settings.web.tailscale_hosts` 로 제한하고(userinfo `@` 든 Host 는 거부), 루프백 밖 Host 는 `tailscale_users` 가 있으면 `Tailscale-User-Login` 이 그 목록에 있어야 한다. ① 조회(GET)는 토큰 없음 ② 쓰기(POST/PATCH/DELETE)는 `require_token`(Bearer `WEB_TOKEN`, 미설정이면 닫힘) + `require_same_origin` ③ 키 입력·토큰 받기·계좌 추가삭제는 `require_direct_local` — 루프백 클라이언트 ∧ 루프백 Host ∧ 프록시 흔적 없음(`tailscale-user-login`·`forwarded`·`x-real-ip`·이름이 `x-forwarded-` 로 시작하는 모든 헤더). 실전 계좌 동작(모드 변경·승인·실전 auto 가 있을 때 재개·trading 큐 kind)은 본문 `confirm == 계좌 id`(`require_confirm`); 대상 계좌 없는 trading kind 는 실전 계좌가 있으면 `confirm` 이 실전 계좌 id 중 하나여야 하고 계좌 포트가 없으면 거절한다.
  - 모든 응답에 `X-Server-Time`(ISO-8601 UTC), SSE ping 은 `: ping {"server_time": …}`. 베어 `/api`·모르는 `/api/*` 는 메서드 무관 404.
  - **SSE 스냅샷**: 커널 키 `daemon`(노이즈, `{alive, minute}`) + `KERNEL_PROBES`(`usage`·`control`(값 기반 해시, 커서·TTL 플래그 `CONTROL_CURSOR_LIKE` 제외)·`jobs`·`runs`·`notices`) + 기능 `Feature.sse_keys`. `CONTROL_CURSOR_LIKE` 가 기능 키 접두(`news_trigger_at:%`·`ws_session_busy:%`)를 품는 것은 알려진 약한 위반이다(T10-R3, 기능 선언으로 옮길 후보).
  - **merge 자원**: 여러 기능이 같은 경로의 `ResourcePart(path, fn)` 를 내면 `merge.mount` 가 `GET` 하나로 합친다. 응답 `{"items", "partial"}` — 조각이 예외·비목록이면 나머지만 내고 `partial=True`. `items` 는 `at` 을 ISO 로 읽어 역순(날짜만은 00:00 UTC, 못 읽으면 끝). `?format=csv` 면 같은 items 를 CSV. 경로 변수·쿼리는 조각에 그대로 넘긴다.
  - **생성 파일**: `web/src/generated/sse-keys.ts`(`gen.sse_keys_ts`)·`nav.ts`(`gen.nav_ts`, 커널 고정 5 화면 + `Feature.nav` 의 `NavItem(screen, id, label, order)`, id 는 전역 유일)는 `dev gen-sse-keys`·`dev gen-nav` 로 만들고 `--check` 와 CI 동기 테스트가 지킨다.
  - **웹 프로세스 분리**: `trader web` 은 `boot(ports=True)` 로 포트를 조립하되 `build_web_deps` 가 주문 집행 포트(`stock_exec`, 그리고 `execute`·주문 이름의 호출을 가진 포트)를 뺀 사본만 넘기고, accounts 포트는 조회 메서드(`accounts`·`fo_accounts`·`paused`·`policy`)만 위임하는 `ReadOnlyAccounts` 로 감싼다(`main.web_ports` — `brokers`·`fo_brokers`·`market_client` 없음). 웹 라우터는 브로커·알림 객체를 쥐지 않고 쓰기는 원장 + 큐(`job_requests`)뿐 — 실제 집행·알림은 데몬이 큐를 꺼내 한다. 예외: `quotes` 허브는 표시용 KIS 시세 클라이언트를 쓴다(앱키·토큰 캐시 공유 `TRADER_KIS_SHARED_DIR`).
  - **웹이 쓰는 control 키**: `usage_throttle`(커널 `PATCH /settings/usage-throttle`)·`saveticker_restart_requested`(데몬 saveticker 잡이 소비)·`fx_cache:<pair>`(quotes 하루 캐시, 원천 실패 때 `fail_at` 음성 캐시 300초)·`paused`·`watchlist`·계좌 모드(`account_modes`) — 변경과 조작(수동 작업 요청·취소 `job:<kind>`, 계좌 추가·삭제 `account:<id>`, 연결 키 변경 `secrets:env` 키 이름만; 텔레그램 `/run` 도 `job:<kind>` channel `telegram`)은 `control.log_change`(channel `web`, actor = 토큰 지문)로 `control_log` 에 남는다. `ws_session_busy:<env>` 는 데몬 체결통보 리스너가 쓰고 웹 시세 허브가 읽는다(세션 양보).
  - **12 자원 소유표**(스펙 §2.5, 대응 검사 `tests/kernel/web/test_endpoint_map.py` — 첫 세그먼트 → 자원 별칭표 포함):

    | 자원 | 경로 | 소유 |
    |---|---|---|
    | accounts | `/accounts`, `/accounts/overview`, `/accounts/{id}` (PATCH·POST·DELETE) | accounts |
    | positions | `/accounts/{id}/positions`(merge: spot+scalp+fo), `/positions/{overview,risk}`, `/accounts/{id}/tax-lots` | spot·scalp·fo·evaluation |
    | decisions | `/decisions`(merge), `/decisions/{spot,fo}/{id}[/trace…]`, `/markets/{m}/today`, `/risk-checks`, `/fo/today` | spot·fo |
    | orders | `/orders`(merge), `/fo/legs`, `/fo/summary` | spot·fo |
    | approvals | `/approvals`, `PATCH /approvals/{id}` | spot |
    | jobs | `/jobs`(+POST), `/jobs/requests`, `DELETE /jobs/{id}`, `/runs`, `/calls` | 커널 |
    | events | `/events`(SSE) | 커널 |
    | news | `/news`, `/news/{events,themes,labels,manual}` | ingest·scan |
    | quotes | `/quotes`, `/quotes/{indices,book,fo,fx}`, `/names*`, `/charts*`, `/features/*`, `/ml/models*` | quotes·ml |
    | settings | `/settings`, `/settings/{paused,watchlist,usage-throttle,secrets}` | accounts·scan·커널 |
    | system | `/system/{health,status,usage,daemon,notices,control-log,regime,saveticker,scalp,fo}`, `POST /system/{bootstrap-token,saveticker/restart,usage/ensure-fresh}` | 커널·regime·saveticker·scalp·fo |
    | analytics | `/analytics/performance*`, `/analytics/{shadow,calibration,memory}`, `/analytics/rl/*`, `/analytics/fo/*` | evaluation·fo |
  - **스펙 §2.5 대비 알려진 차이**: `/decisions/{id}` → `/decisions/spot/{id}`·`/decisions/fo/{id}`; `/tax-lots` → `/accounts/{id}/tax-lots`; `/rl/*` → `/analytics/rl/*`; `/fo/{scorecard,report,summary}` → `/analytics/fo/*`(`/fo/summary` 도 남김); `/ml/*`·`/names*`·`/charts*`·`/features*`·`/markets/{m}/today`·`/risk-checks`·`/fo/legs|today` 는 자기 첫 세그먼트를 유지(별칭표로 자원에 묶음); `/system/ops` → `/system/health`(임시 판정, M12 `kernel/ops` 로 교체); 옛 `/fo/account` → `/system/fo`; `/quotes/us` 없음(`/quotes?market=US`); 플랜 추가 `GET /accounts/overview`·`/system/notices`·`/system/control-log`·`/quotes/fx`·`/quotes/fo`; 스펙 밖 추가 `/news/labels`·`GET /news/manual`·`/decisions/spot/news-refs`·`POST /system/usage/ensure-fresh`(같은 출처만, 토큰 없음 — R1). 자원별 세부 차이는 각 기능 README 「웹」 절.
- 테스트: `tests/kernel/web/` (+ `web/` vitest)

## cli

- 책임: typer 루트와 부트스트랩. 커널 명령 `init`·`selfcheck`·`daemon`·`web`·`dev`·`notify-test`·`telegram` 과 `tool` 루트를 둔다.
- 공개면: `main`(`app`·`TraderApp`·`build_app`), `boot`(`boot`·`Boot`·`config_errors`), `init_cmd`, `selfcheck`, `dev`(`gen-sse-keys`·`gen-nav`), `tool_cmd`(`build_tool_app`·`run_tool`·`emit`).
- 규약:
  - 명령 부트스트랩(로깅·설정·레지스트리·스키마 적용 원장 연결)은 `boot()` 하나다. 명령마다 따로 조립하지 않는다.
  - 설정 오류는 `config_errors` 데코레이터가 사용자용 메시지로 바꾼다.
  - cli 는 `features` 를 정적 import 하지 않는다. 기능은 `discover()` 를 거쳐 sub-app 으로 마운트된다.
  - `selfcheck` 는 JSON 1줄(파이썬 버전·루트·원장 버전·기능 목록·웹 dist 유무). `init` 은 템플릿 중 없는 파일만 복사한다.
  - 콘솔 UTF-8 강제는 `TraderApp` 진입점에서 한다.
  - `trader tool` 루트(`tool_cmd`): 기능이 선언한 `ToolCommand` 를 모아 단 하나의 그룹으로 마운트한다(이름 중복은 `RegistryError`). 출력은 JSON 1줄뿐이고 `emit` 은 `ok: false` 면 종료 코드 1, `run_tool` 은 예외를 `{"ok": false, "error": "<Type>: <msg>"}` 로 바꾼다. 세션의 허용 명령은 `bundle.tool_command()`(번들 `trader tool` / 개발 `python -m trader tool`)와 같은 문자열이고, 세션 PATH 앞에 `bundle.tool_bin_dir()`(`platform.bundle`, 번들은 실행파일 폴더·개발은 venv bin)를 둔다. claude 실행파일 탐색은 `platform.claude_bin`(`find_claude`·`child_path`·`ClaudeNotFound`).
  - `boot(ports=True, …)` 는 settings·secrets·conn 을 함의하고 모든 `Feature.ports` 를 이름으로 조립해 `Boot.ports` 에 담는다. 텔레그램 `CommandContext.ports` 도 같은 맵이라 핸들러(`/approve` 등)가 `ctx.ports["accounts"]` 로 계좌·브로커·정책을 받는다.
- 테스트: `tests/kernel/cli/`

## notify

- 책임: 텔레그램 발신(outbox 포함)·수신·명령 디스패처·문구 베이스. 기능 이름을 모른다. 기능은 `registry` 에 명령과 `/status` 절을 더한다.
- 공개면: `kernel.notify.__all__` — `Notifier`·`NullNotifier`·`TelegramNotifier`·`TelegramReceiver`·`Update`·`parse_command`·`make_notifier`·`make_receiver`·`CommandContext`·`poll_once`·`messages`(`money`·`pct`·`first_sentences`·`kst_hhmm`·`render`·`event`·`failure`·`mode_changed`, 주문·승인 문구 `DecisionBrief`·`opinion_lines`·`order`·`order_unknown`·`bookkeeping_failure`·`execution_failure`·`approve_hint`·`approval_request`·`approval_rejected`·`approval_tampered`·`open_buys_cancelled`·`throttle_skip` — 이 열둘은 `kernel.notify` 에서도 바로 import 한다). `commands.registry`·`help_line`·`status_message` 는 모듈 경로로 쓴다. 설정 섹션 `settings.notify`(`enabled`, `poll_interval_s` 5..600), `DaemonDeps.notifier`. CLI `trader notify-test`, `trader telegram poll|loop [--interval]`.
- 규약:
  - 토큰은 URL 에 들어가므로 URL·예외 메시지를 로그에 남기지 않는다(`trader.notify`).
  - 메시지는 4000자(`MAX_LEN`)로 분할하고 HTML 이스케이프한다.
  - 발신 실패 시 outbox 에 보관하고 `True` 를 돌려준다. `flush_outbox` 가 재전송하며 보관 24h(`OUTBOX_TTL`), 한 번에 10건(`OUTBOX_FLUSH_MAX`).
  - 수신: 설정된 chat_id 가 아닌 채팅의 메시지는 버린다.
  - 신뢰 판정: `TELEGRAM_ALLOWED_USERS` 가 있으면 그 목록의 발신자만, 없으면 1:1(private) 채팅만 신뢰한다. 신뢰 밖 발신자는 `/status` 와 `untrusted_ok` 명령만 쓸 수 있고 나머지는 "명령 거부" 로 답한다(`commands.poll_once`).
  - 오프셋(`telegram_offset`)은 업데이트마다 전진한다.
  - 모르는 명령은 분당 1회(`HELP_EVERY_S=60`, 플래그 `telegram_help_at`)만 도움말을 보낸다.
  - `/status` 는 내장이고 기능이 `status_sections` 로 절을 더한다. 명령 이름 `status` 는 예약이다. M9 부터 절별로 격리한다 — 절 하나의 예외는 `- {title}: 조회 실패(…)` 한 줄이 되고 나머지 절은 그대로 나온다.
  - `notify.enabled=false` 면 `NullNotifier` 이고 수신기는 없다(리허설).
- 테스트: `tests/kernel/notify/`
- 알려진 차이:
  - 승인 만료는 훅이 아니라 spot 의 `approvals_expire` 잡(M4)과 `approvals list`·`approve` 가 처리한다. 데몬 `telegram_poll`·outbox flush 잡은 M5 로 이연.
  - 옛 `telegram` 로거 이름은 `trader.notify` 로 바뀌었다.
  - `/status` 절별 격리는 M9 에서 들어왔다(그 전에는 절 하나가 실패하면 전체가 실패했다). `SCALP_EXIT` 표는 커널이 아니라 scalp `messages.py` 가 소유한다(M9). `DecisionBrief`·주문·승인 문구는 M4(실행 코어)가 옮겼다. 종목 표기가 필요한 `approval_line` 은 spot `messages.py` 가 소유한다.

## features 층

M8 에서 커널에 더한 것: `session/quick.py`(`ask_json`·`probe_usage`, 단발 LLM 호출), `ledger/backup.py`(sqlite 온라인 백업), 내장 잡 `usage_probe`(1800s)·`ledger_backup`, `execution/costs.py`(`round_trip_bp`), `EvaluationPort`(ports["evaluation"] 계약).

`src/trader/features/<x>/` 는 마일스톤마다 하나씩 채운다. 채워진 기능: `accounts`(M2, 계좌·모드·LIVE 확인·키, `features/accounts/README.md`), `ingest`(M3, KIS·DART·EDGAR·뉴스·커뮤니티·거시 수집과 `trader ingest collect`, `features/ingest/README.md`), `saveticker`(M3, 세이브티커 Chromium 수집기·리더·데몬 잡·Chrome 확장, `features/saveticker/README.md`·`docs/saveticker-collection.md`). 기능 목록·계층·옛 출처 표는 스펙 §2.1, 계약 필드는 §2.3, 이식 순서는 §3 을 본다. `spot`(M4, 레인: 종목 분석 세션·리스크 게이트·승인 경로, `features/spot/README.md`). `regime`(M6, 서비스: 국면·킬스위치·권리 일정 블랙아웃·시장 세션·`{market}_regime`/`ksd_events` 잡·`ports["regime"]`·`trader regime`·텔레그램 `/kill`, `features/regime/README.md`), `scan`(M6, 서비스: 테마 열기·사건 감지·`news.post_ingest` 훅·`ports["scan"]`·`trader scan`, `features/scan/README.md`). 레인(spot)은 두 기능을 `LaneContext.ports["regime"]`·`ports["scan"]` 로만 받고 import 하지 않는다. spot 에는 헤지·현금 운용·주간 리밸런스(M6)도 있다. `ml`(M7, 서비스: 피처 스토어·퀀트 학습·튠·레지스트리·시점 유니버스·`05_quant` 리포트·`ml_train`/`universe_members` 잡·큐 kind `features_*`/`ml_train`·`ports["ml"]`·`trader ml`, `features/ml/README.md`). `scan` 은 M7 에서 정량 필터·ML 랭커·발굴·스크리너·뉴스/공시 트리거·관심종목(`/watch`)·유니버스 점검·`trader scan run|backtest|universe`로 확장됐다(ML 랭커는 ml→scan 순환을 피해 scan 소유). spot 에는 `{market}_scan_analyze` 잡·큐 kind `scan_analyze`/`analyze_ticker`(M7)가 들어왔다. 옛 `features/jobs` 는 별도 기능이 되지 않고 커널 작업 큐로 흡수됐다(`QueueKind` 선언만 각 기능이 더하고 `/run` 은 커널 내장). `scalp`(M9, 레인: 분 단위 단타·모의 전용·기본 끔 — `{market}_scalp` 잡·판정자 `scalp-judge`·`ports["scalp"]`·`trader scalp backtest|report`·`/status` 「단타(모의 전용)」 절, `features/scalp/README.md`)는 주문을 spot 이 공급하는 `ports["stock_exec"]` 로만 내고 `accounts`·`regime`·`scan` 도 포트로 받는다. `fo`(M10, 레인: 코스피200 선물·옵션 방향성 — 모의 파생 계좌 전용·기본 끔, 자체 실행기·`LegStore`·상태기계·장중 모니터/재평가, `kr_fo_session|monitor|reconcile` 잡·큐 kind `fo_*`·세션 `fo`/`fo_resume`·`trader fo …`·`/fo`, `features/fo/README.md`)는 주문을 자기 실행기로 내고 `accounts`·`regime` 을 포트로 받으며 뉴스·거시 입력은 M11 `InputsPort.recent` 까지 비어 있다. 기능 골격과 import 규칙은 `CLAUDE.md` 를 따른다.
