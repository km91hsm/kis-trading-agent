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
- 공개면: `load`·`memo_load`, `Section`·`SectionRegistry`·`ConfigFile`, `kernel_registry`·`KERNEL_SECTIONS`, `ConfigError`, `KST`, `Market`, `WebSettings`.
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

## registry

- 책임: 기능 계약(`Feature`)과 그 타입, 기능 발견, 계약·import 경계 검사.
- 공개면: `Feature`·`JobSpec`·`Hook`·`SseKey`·`HealthArea`·`StatusSection`·`TelegramCommand`·`ToolCommand`·`StageRule`·`OriginSpec`·`QueueKind`·`SessionKind`, `RegistryError`, `discover`, `check`, `import_violations`.
- 규약:
  - `discover()` 는 `trader.features` 아래 각 패키지의 `feature.FEATURE` 를 `importlib`+`pkgutil` 로 동적 import 한다. kernel 이 features 를 import 하는 유일한 경로다.
  - import 3층 규칙: `kernel` ← 서비스 기능 ← 레인. 다른 기능은 `api.py` 로만, 레인은 기능 import 금지, 커널은 기능 import 금지(`discover` 제외). `import_violations` 가 AST 로 검사하며 `from pkg import features`·상대 import 형태도 잡는다.
  - `check(features, roots=None)`: `roots` 기본값은 `trader.features.__path__`. 기능 id·잡 id·큐 kind·SSE 키·origin 중복, 기능 폴더 README 존재, 마이그레이션 규칙을 메시지 목록으로 돌려준다.
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

- 책임: typer 루트와 부트스트랩. 커널 명령 `init`·`selfcheck`·`daemon`·`web`·`dev` 를 둔다.
- 공개면: `main`(`app`·`TraderApp`·`build_app`), `boot`(`boot`·`Boot`·`config_errors`), `init_cmd`, `selfcheck`, `dev`(`gen-sse-keys`).
- 규약:
  - 명령 부트스트랩(로깅·설정·레지스트리·스키마 적용 원장 연결)은 `boot()` 하나다. 명령마다 따로 조립하지 않는다.
  - 설정 오류는 `config_errors` 데코레이터가 사용자용 메시지로 바꾼다.
  - cli 는 `features` 를 정적 import 하지 않는다. 기능은 `discover()` 를 거쳐 sub-app 으로 마운트된다.
  - `selfcheck` 는 JSON 1줄(파이썬 버전·루트·원장 버전·기능 목록·웹 dist 유무). `init` 은 템플릿 중 없는 파일만 복사한다.
  - 콘솔 UTF-8 강제는 `TraderApp` 진입점에서 한다.
- 테스트: `tests/kernel/cli/`

## features 층 (예정)

`src/trader/features/<x>/` 는 M1 이후 마일스톤마다 하나씩 채운다. 기능 목록·계층·옛 출처 표는 스펙 §2.1, 계약 필드는 §2.3, 이식 순서는 §3 을 본다. 기능 골격과 import 규칙은 `CLAUDE.md` 를 따른다.
