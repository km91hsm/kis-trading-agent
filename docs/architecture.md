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
- 공개면: `load`·`memo_load`, `Section`·`SectionRegistry`·`ConfigFile`, `kernel_registry`·`KERNEL_SECTIONS`, `ConfigError`, `KST`, `Market`, `WebSettings`, `NotifySettings`, `secrets`(`load_secrets`·`Secrets`).
- 규약:
  - `Section.model` 은 pydantic **필드 주석**이다(`list[Market]` 같은 타입도 허용). RootModel `.root` 를 강요하지 않는다.
  - 모든 섹션 모델은 `extra="forbid"`. 알 수 없는 키는 `ConfigError`.
  - `kernel_registry()` 는 호출마다 커널 섹션만 담은 새 레지스트리를 준다. 부트가 복제본에 `feature.sections` 를 등록한다.
  - `KST` 는 `config/time.py` 하나. 다른 곳에서 `ZoneInfo("Asia/Seoul")` 를 만들지 않는다.
  - `templates/config/*.yaml` 은 스키마를 통과해야 한다(자리표시자만).
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
- 테스트: `tests/kernel/ledger/`

## session

- 책임: 실행 체크포인트 저장소(`kernel/session/run_store.py`). 레이아웃은 `state/runs/<운영일>/<시장>/<종목>/<이름>.json`. M4 이후 단계 파일은 이 함수들로만 읽고 쓴다.
- 공개면: `run_date_for`·`run_dir`·`market_dir`, `CHECKPOINT_SCHEMA_VERSION`(=1)·`checkpoint_payload`, `write_checkpoint`·`read_checkpoint`·`list_checkpoints`.
- 규약:
  - 운영일 `run_date_for(market, now)`: KR 은 KST 날짜. US 는 현지 날짜의 개장 시각(09:30 ET)을 KST 로 바꾼 날짜라서, 개장 전 analyze 와 KST 자정 뒤 장중 monitor·재평가가 같은 디렉터리를 본다.
  - `run_dir(d, market, ticker, state=None)` 은 종목 디렉터리를 만들어 돌려주고 `state/runs` 밖을 가리키면 `ValueError`. `market_dir(..., create=False)` 은 시장 단위 산출물(`_scan`·`_screen`·`_reflect`) 자리.
  - 체크포인트 봉투 `checkpoint_payload(data, call=None)` = `{schema_version, data, call}`.
  - `write_checkpoint` 는 pid·스레드 접미사 임시파일에 쓴 뒤 `atomic_replace` 로 교체한다(데몬의 monitor·작업 대기열이 다른 스레드라 pid 만으로는 임시파일이 겹친다).
  - `read_checkpoint` 는 없으면 `None`, 이름이 디렉터리 밖을 가리키면 `ValueError`. `list_checkpoints` 는 이름 정렬.
- 테스트: `tests/kernel/session/test_run_store.py`

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
- 테스트: `tests/kernel/broker/` (HTTP 는 respx, fixture JSON 은 합성 응답)

## registry

- 책임: 기능 계약(`Feature`)과 그 타입, 기능 발견, 계약·import 경계 검사.
- 공개면: `Feature`·`JobSpec`·`Hook`·`SseKey`·`HealthArea`·`StatusSection`·`TelegramCommand`·`ToolCommand`·`StageRule`·`OriginSpec`·`QueueKind`·`SessionKind`, `RegistryError`, `discover`, `check`, `import_violations`.
- 규약:
  - `discover()` 는 `trader.features` 아래 각 패키지의 `feature.FEATURE` 를 `importlib`+`pkgutil` 로 동적 import 한다. kernel 이 features 를 import 하는 유일한 경로다.
  - import 3층 규칙: `kernel` ← 서비스 기능 ← 레인. 다른 기능은 `api.py` 로만, 레인은 기능 import 금지, 커널은 기능 import 금지(`discover` 제외). `import_violations` 가 AST 로 검사하며 `from pkg import features`·상대 import 형태도 잡는다.
  - `check(features, roots=None)`: `roots` 기본값은 `trader.features.__path__`. 기능 id·잡 id·큐 kind·SSE 키·origin 중복, 기능 폴더 README 존재, 마이그레이션 규칙을 메시지 목록으로 돌려준다.
  - 훅: `hooks.by_point(features)` 가 각 `Feature.hooks` 를 포인트별 튜플로 묶고(기능 순서·선언 순서 유지), `hooks.run(hooks, point, **kw)` 가 차례로 부른다. 훅 하나의 예외는 기록하고 다음 훅을 계속하며(실패 격리), 훅마다 `{"hook","result"}` 또는 `{"hook","error"}` 한 건을 돌려준다. 포인트는 `news.post_ingest` 등. 커널은 훅 내용을 모른다.
  - 이름이 `features` 인 기능 폴더는 두지 않는다(루트 추론이 `<root>/features` 존재에 의존).
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
  - 큐 레인 4개·heavy 락·미스파이어 정책은 M4 이후(JobSpec 속성만 통과시킨다).
- 테스트: `tests/kernel/daemon/`

## web

- 책임: FastAPI 셸. 인증 3등급·루프백 Host 게이트·기능 라우터 마운트·SSE 지문 조립·정적 dist. 기능 이름은 모른다.
- 공개면: `create_app`, `WebDeps`, `require_token`·`require_direct_local`·`require_same_origin`·`is_direct_local`, `sse`(`snapshot`), `gen`(`sse_keys_ts`).
- 규약:
  - 인증: 토큰 / 직접 로컬 / same-origin 의 3등급을 라우트 의존성으로 건다.
  - 기능 라우터는 `feature.routes` 만 마운트한다. 기능이 없으면 `/api/system/health`·`/api/events` 만 있다.
  - SSE 스냅샷은 레지스트리의 `SseKey` 로 조립하고 커널 키 `daemon` 의 지문은 `{alive, minute}`(노이즈 키).
  - `web/src/generated/sse-keys.ts` 는 `gen` 이 만든다. 최신 여부는 `dev gen-sse-keys --check` 로 검사한다.
- 테스트: `tests/kernel/web/` (+ `web/` vitest)

## cli

- 책임: typer 루트와 부트스트랩. 커널 명령 `init`·`selfcheck`·`daemon`·`web`·`dev`·`notify-test`·`telegram` 을 둔다.
- 공개면: `main`(`app`·`TraderApp`·`build_app`), `boot`(`boot`·`Boot`·`config_errors`), `init_cmd`, `selfcheck`, `dev`(`gen-sse-keys`).
- 규약:
  - 명령 부트스트랩(로깅·설정·레지스트리·스키마 적용 원장 연결)은 `boot()` 하나다. 명령마다 따로 조립하지 않는다.
  - 설정 오류는 `config_errors` 데코레이터가 사용자용 메시지로 바꾼다.
  - cli 는 `features` 를 정적 import 하지 않는다. 기능은 `discover()` 를 거쳐 sub-app 으로 마운트된다.
  - `selfcheck` 는 JSON 1줄(파이썬 버전·루트·원장 버전·기능 목록·웹 dist 유무). `init` 은 템플릿 중 없는 파일만 복사한다.
  - 콘솔 UTF-8 강제는 `TraderApp` 진입점에서 한다.
- 테스트: `tests/kernel/cli/`

## notify

- 책임: 텔레그램 발신(outbox 포함)·수신·명령 디스패처·문구 베이스. 기능 이름을 모른다. 기능은 `registry` 에 명령과 `/status` 절을 더한다.
- 공개면: `kernel.notify.__all__` — `Notifier`·`NullNotifier`·`TelegramNotifier`·`TelegramReceiver`·`Update`·`parse_command`·`make_notifier`·`make_receiver`·`CommandContext`·`poll_once`·`messages`(`money`·`pct`·`first_sentences`·`kst_hhmm`·`render`·`event`·`failure`·`mode_changed`). `commands.registry`·`help_line`·`status_message` 는 모듈 경로로 쓴다. 설정 섹션 `settings.notify`(`enabled`, `poll_interval_s` 5..600), `DaemonDeps.notifier`. CLI `trader notify-test`, `trader telegram poll|loop [--interval]`.
- 규약:
  - 토큰은 URL 에 들어가므로 URL·예외 메시지를 로그에 남기지 않는다(`trader.notify`).
  - 메시지는 4000자(`MAX_LEN`)로 분할하고 HTML 이스케이프한다.
  - 발신 실패 시 outbox 에 보관하고 `True` 를 돌려준다. `flush_outbox` 가 재전송하며 보관 24h(`OUTBOX_TTL`), 한 번에 10건(`OUTBOX_FLUSH_MAX`).
  - 수신: 설정된 chat_id 가 아닌 채팅의 메시지는 버린다.
  - 신뢰 판정: `TELEGRAM_ALLOWED_USERS` 가 있으면 그 목록의 발신자만, 없으면 1:1(private) 채팅만 신뢰한다. 신뢰 밖 발신자는 `/status` 와 `untrusted_ok` 명령만 쓸 수 있고 나머지는 "명령 거부" 로 답한다(`commands.poll_once`).
  - 오프셋(`telegram_offset`)은 업데이트마다 전진한다.
  - 모르는 명령은 분당 1회(`HELP_EVERY_S=60`, 플래그 `telegram_help_at`)만 도움말을 보낸다.
  - `/status` 는 내장이고 기능이 `status_sections` 로 절을 더한다. 명령 이름 `status` 는 예약이다.
  - `notify.enabled=false` 면 `NullNotifier` 이고 수신기는 없다(리허설).
- 테스트: `tests/kernel/notify/`
- 알려진 차이:
  - 옛 `expire_approvals` 훅은 M4 가 더한다.
  - 데몬 `telegram_poll`·outbox flush 잡은 M4.
  - 옛 `telegram` 로거 이름은 `trader.notify` 로 바뀌었다.
  - `/status` 는 절 하나가 실패하면 전체가 실패한다. `SCALP_EXIT` 표·`DecisionBrief` 는 이식하지 않았다(M4/M5/M9).

## features 층

`src/trader/features/<x>/` 는 마일스톤마다 하나씩 채운다. 채워진 기능: `accounts`(M2, 계좌·모드·LIVE 확인·키, `features/accounts/README.md`), `ingest`(M3, KIS·DART·EDGAR·뉴스·커뮤니티·거시 수집과 `trader ingest collect`, `features/ingest/README.md`), `saveticker`(M3, 세이브티커 Chromium 수집기·리더·데몬 잡·Chrome 확장, `features/saveticker/README.md`·`docs/saveticker-collection.md`). 기능 목록·계층·옛 출처 표는 스펙 §2.1, 계약 필드는 §2.3, 이식 순서는 §3 을 본다. 기능 골격과 import 규칙은 `CLAUDE.md` 를 따른다.
