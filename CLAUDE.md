# kis-trading-agent

한국투자증권(KIS) OpenAPI 자동매매 에이전트. 리스크 판정과 주문 집행은 파이썬 코드가 강제하고, LLM(`claude -p`)은 `trader tool ...` 도구만 호출한다. 이 파일은 코드가 담지 못하는 규칙만 둔다. 나머지는 타입·선언 표·계약 테스트·기능 README 가 정본이다(스펙 §2.6).

## 실행 명령

| 명령 | 용도 |
|---|---|
| `uv sync` | 의존성 설치 |
| `uv run pytest -q` | 파이썬 테스트 전체 |
| `uv run ruff check src tests` | 린트 |
| `uv run trader init` | `config/ secrets/ state/ docs/` 생성, 템플릿 중 없는 파일만 `config/` 로 복사 |
| `uv run trader selfcheck` | 환경·원장 버전·기능 목록을 JSON 1줄로 출력 (세션 시작 때 먼저 실행) |
| `uv run trader daemon [--dry-run]` | 스케줄러 데몬 (사용자 전용 — 금지사항 참고) |
| `uv run trader web [--host] [--port]` | 웹 UI·API 서버 |
| `uv run trader accounts show\|mode\|pause\|resume\|ping\|add\|remove\|secrets import` | 계좌 목록·모드·일시정지·점검·추가삭제·키 가져오기 (모드 변경·`ping` 은 사용자 전용) |
| `uv run trader ingest collect --ticker … [--market KR\|US --exchange … --asof … --force]` | 종목 수집 1회(공시·뉴스·거시 등, 절별 오류 격리) |
| `uv run trader spot analyze --ticker … [--market KR\|US --exchange … --asof … --dry-run --skip-collect]` | 종목 1개 분석 세션 → `30_decision` (`FAKE_CLAUDE=1` 없이 금지 — 금지사항) |
| `uv run trader spot approvals list\|approve\|reject` | 승인 대기 조회·승인·거부 (`approve` 는 사용자 전용) |
| `uv run trader spot exec-key list\|release` | 실행 키(멱등 선점) 조회·수동 해제 (`release` 는 KIS 주문 유무 확인 뒤 사용자가) |
| `uv run trader spot monitor --market KR\|US [--once --interval 60 --dry-run]` | 보유종목 장중 점검(손절 즉시 매도·트리거 재평가) — 자동 주문이 나가므로 사용자 전용 |
| `uv run trader spot fills sync [--account … --date …]` | 체결 동기화(원장 적재, 멱등, 계좌마다 JSON 1줄) |
| `uv run trader spot balance refresh [--market KR\|US --force]` | 잔고 스냅샷 갱신(조회만) |
| `uv run trader spot reconcile --market KR\|US` | 브로커 보유 vs 스냅샷 대사(스냅샷만 보정, 주문 유지) |
| `uv run trader regime show\|backtest --market KR\|US --days N\|killswitch on [--reason]\|off --note …\|show` | 국면·킬스위치 조회·백테스트·수동 발동/해제 (`killswitch on`·`off` 는 사용자 전용) |
| `uv run trader scan theme show [--market KR\|US --live]\|events show [--market KR\|US]` | 테마 열기·사건 감지 결과 조회 (조회만) |
| `uv run trader scan run [--market KR\|US --asof … --dry-run]\|backtest\|universe check\|universe sectors` | 정량 필터→screener 세션 스캔(`FAKE_CLAUDE=1` 없이 금지)·후보 백테스트(조회만)·유니버스 시세 점검·섹터 제안 |
| `uv run trader scalp backtest --ticker … [--market KR\|US --exchange … --days N]\|report [--market KR\|US --days N --live]` | 단타 1분봉 규칙 재현·실적/판정자 사후 비교 JSON (조회만, 주문 없음; 단타는 모의 전용·기본 끔) |
| `uv run trader fo contracts\|chain\|verify --account …\|session [--dry-run --asof …]\|status\|adopt …` | 파생(선물옵션, 모의 전용·기본 끔) 계약·체인 조회·검증 플래그·아침 판정 세션(`FAKE_CLAUDE=1` 없이 금지)·상태·unknown 포지션 승계. `monitor\|close\|order-test` 는 주문이 나가므로 사용자 전용 |
| `uv run trader ml train\|tune\|models\|promote\|demote` | 퀀트 모델 학습·튜닝 리포트·레지스트리 조회·수동 승격/해제 (`promote`·`demote` 는 사용자 전용) |
| `uv run trader ml features snapshot\|backfill\|derive` | 피처 스토어 오늘 스냅샷·과거 백필·파생 열 재계산 |
| `uv run trader jobs list [--limit]\|run <kind> [--param k=v … --priority n]\|kinds` | 잡 큐 조회·요청 등록(데몬 실행 중이어야 `run` 가능)·요청 가능한 kind 목록 |
| `uv run trader tool …` | 세션 전용 — LLM 이 쓰는 도구 명령(JSON 1줄). 사람이 직접 돌리지 않는다 |
| `uv run trader saveticker status\|query\|export\|run\|service\|monitor\|install-browser\|extension {prepare\|install-host\|uninstall-host\|status}` | 세이브티커 수집·조회·Chrome 확장 (`run`·`service`·`install-browser` 는 사용자 전용) |
| `uv run trader notify-test` | 텔레그램 시험 메시지 1건 |
| `uv run trader evaluation reflect\|readiness\|backtest\|paper-review\|calibration build\|rl {train\|models\|report\|promote\|demote}` | 복기·준비도·백테스트·페이퍼 리뷰·보정·RL 사이저 (`reflect` 는 `FAKE_CLAUDE=1` 없이 금지, `rl promote\|demote` 는 사용자 전용) |
| `uv run trader telegram poll\|loop [--interval]` | 텔레그램 명령 수신 1회 / 반복 (내장 `/run <kind>` 큐 요청·`/daily` 일일 리포트·`/watch add\|remove\|list` 관심종목 포함) |
| `uv run trader dev gen-sse-keys [--check]` | `web/src/generated/sse-keys.ts` 재생성 / 최신 여부 검사 |
| `uv run trader dev gen-nav [--check]` | `web/src/generated/nav.ts`(기능 `nav` 메뉴) 재생성 / 최신 여부 검사 |
| `cd web && npm ci && npm run typecheck && npm test -- --run && npm run build` | 프런트 검증 (CI 와 같음) |
| `cd web && npm run dev` | 프런트 개발 서버(5173, `/api` 는 `trader web --port 8787` 로 프록시). 구조·실시간·인증은 `docs/architecture.md` 「프런트(web/)」 |

## 아키텍처 불변식

여러 파일에 걸쳐 있어 한 곳을 읽어서는 알 수 없는 규칙만 적는다. 한 줄 = 규칙 + 정본 위치.

- **두 층**: `src/trader/kernel/` 은 기능 이름을 모르고, `src/trader/features/<x>/` 가 기능이다. 커널은 `registry.discover()` 로만 기능을 만난다. (`docs/architecture.md`, 스펙 §2.1)
- **import 3층**: `kernel` ← 서비스 기능 ← 레인(spot·fo·scalp). 아래쪽으로만, 다른 기능은 그 기능의 `api.py` 만, 레인은 어떤 기능도 import 하지 않는다(필요한 것은 deps 주입). 커널에는 Protocol·DTO 만. (`registry.import_violations`, 스펙 §2.7-1)
- **기능 등록은 한 곳**: `features/<x>/feature.py` 의 `FEATURE`. 잡·라우터·CLI·설정 섹션·마이그레이션·SSE 키는 모두 거기서 선언하고 커널이 수집한다. (`registry.contracts.check`)
- **마이그레이션**: 기능별 파일에 두되 버전은 전역 오름차순, 추가만(기존 항목 수정·삭제·중간 삽입 금지). 위반은 `ledger.migrations.check` 가 막는다.
- **설정 섹션**: yaml 키는 `config` 의 섹션 레지스트리에 등록한 pydantic 모델(`extra="forbid"`)로만 읽는다. yaml 키를 더하면 모델과 `templates/config/*.yaml` 을 같이 바꾼다(`tests/kernel/config/test_templates.py` 가 강제).
- **생성 파일**: `web/src/generated/sse-keys.ts`·`nav.ts` 는 생성 파일이다. 손으로 고치지 말고 `dev gen-sse-keys`·`dev gen-nav` 로 만든다. CI 게이트는 `tests/kernel/web/test_app.py::test_gen_sse_keys_matches_committed_file`·`test_gen_nav_matches_committed_file` 이고 `--check` 는 그 로컬 등가물이다.
- **데몬 잡 설정**: 잡 시각은 `schedule.yaml` 의 점 경로 키(`JobSpec.schedule_key`)로 가리킨다. 시각을 코드에 박지 않는다.
- **LLM 은 도구만**: LLM 은 주문·원장·파일을 직접 건드리지 않고 `trader tool` 명령(전제조건 표 `StageRule`)만 쓴다. 허용 목록은 `tool_command()` 와 일치해야 한다. (스펙 §2.3)
- **주문은 executor 코어에서만**: 모든 주문은 `kernel/execution` 의 코어를 지난다(게이트·멱등 claim·승인·기록). 기능이 KIS 주문 API 를 직접 부르지 않는다. 예외 하나: 파생(fo)은 다리 상태·`orders` 한 트랜잭션 때문에 자체 실행기(`features/fo/executor.py`, `LegStore`)를 쓴다. (스펙 §2.1, `features/fo/README.md` A1)
- **`dry_run` 은 env**: 주문 전송 차단 여부는 `--dry-run` 이 정하는 환경변수 하나로 하위 프로세스(LLM 세션 포함)까지 전달하고, 코드 경로마다 따로 두지 않는다. (스펙 §2.6)
- **실전 auto 는 LIVE 확인**: 실전 계좌를 `auto` 로 두려면 LIVE 확인이 선행돼야 한다. (스펙 §2.1 `features/accounts`)
- **룩어헤드 금지**: 판정·학습·백테스트는 그 시점에 알 수 있던 데이터만 쓴다(유니버스 PIT 포함). (피처 `latest_rows(asof)`·유니버스 PIT·라벨 엠바고)
- **paused**: `control` 의 `paused` 가 켜지면 신규 주문 경로는 모두 멈춘다.
- **웹은 원장 + 큐만**: 웹 라우터는 브로커·알림을 쥐지 않고(주문 집행 포트는 `build_web_deps` 가 뺀다) 쓰기는 원장·`job_requests` 뿐, 집행은 데몬이 한다. 예외는 `quotes` 시세 조회. 실전 쓰기는 `confirm == 계좌 id`. (`docs/architecture.md` web)

## 금지사항

- 실전 주문을 수동으로 실행하지 않는다. 자동 주문·로그온 등록 경로(`daemon` 의 실기 기동, `kis order *`, `approvals approve`, `install` 등)는 사용자만 실행한다. 실기 검증이 필요하면 사용자에게 요청한다.
- `secrets/` 를 커밋하지 않는다. 읽지도 않는다(`cat` 같은 Bash 읽기 명령 포함, 다른 명령과 묶지 않는다). `config/accounts.yaml` 에는 실제 계좌번호가 있으므로 값을 출력·인용하지 않는다.
- 계좌 모드(`control`/`account_modes`)를 임의로 변경하지 않는다. 모드 변경은 사용자가 한다.
- `FAKE_CLAUDE=1` 없이 `uv run trader spot analyze`·`uv run trader evaluation reflect` 를 돌리지 않는다(구독 호출 발생). 사용자 요청 없이 실제 세션을 띄우지 않는다.
- `trader fo monitor|close|order-test` 는 파생 주문이 나가므로 돌리지 않는다(사용자 전용). `FAKE_CLAUDE=1` 없이 `trader fo session` 도 돌리지 않는다(구독 호출 발생).
- `trader saveticker run|service|install-browser` 는 사이트 로그인·Chromium 다운로드라 사용자 요청 없이 돌리지 않는다.
- 셸 스크립트(`.sh`/`.bat`/`.ps1`)를 추가하지 않는다. 훅·설치·유틸·빌드는 전부 파이썬.
- `state/` 파일을 직접 편집하지 않는다.
- 저장소는 공개다. 외부 사이트에서 수집한 원문(기사 제목·본문·리포트 텍스트)을 테스트 fixture 로 커밋하지 않는다. 구조만 본뜬 합성 데이터를 쓴다.
- `templates/` 에는 자리표시자(`00000000` 등)만 커밋한다. `config/ secrets/ state/` 는 `.gitignore` 대상이다.
- 같은 헬퍼를 모듈마다 다시 쓰지 않는다. 먼저 `kernel/` 에서 찾는다(경로 `platform.paths`, 시각 `config.KST`, 연결 `ledger.connect`, 시크릿 `config.secrets.load_secrets`, 알림 문구 `notify.messages`, KIS 응답 파싱 `broker._parse`(dec·to_int·rows), 호가단위 `broker.ticks.quantize_price`, KIS 클라이언트 조립 `broker.factory.client_for/build_market/build_broker`).

## 코딩 규칙

- 경로는 전부 `pathlib`, 하드코딩 절대경로 금지. 루트는 `platform.paths` 로만 얻는다(`TRADER_HOME` 은 절대경로로 resolve, 없으면 cwd 에서 위로 `pyproject.toml` 탐색). OS 분기는 `platform/` 만.
- 파일명에 `:` 금지(타임스탬프는 `YYYYMMDD_HHMMSS`). 저장 시각은 UTC ISO8601, 표시만 KST/ET. 타임존 객체는 `config.KST` 하나만 쓰고 모듈마다 `ZoneInfo` 를 만들지 않는다. 콘솔 출력은 UTF-8.
- subprocess 는 `shell=False, encoding="utf-8", errors="replace"`.
- SQLite 는 WAL + 쓰기 타임아웃 30초. 금액은 `Decimal`. pydantic 모델은 `extra="forbid"`.
- 사용자 대면 메시지·로그·docstring 은 한국어. 로거 이름은 `trader.<계층>`. 새 의존성은 Windows 휠이 있는 것만.
- TDD: 실패 테스트를 먼저 쓰고 실행해 RED 를 확인한 뒤 구현한다. 테스트는 `tmp_path` 만 쓰고 네트워크는 막는다. 마일스톤 단위 브랜치 `feat/m<N>-<이름>`, PR 대상 main.
- 커밋 트레일러: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## 기능을 더하는 법

`features/<x>/` 에 `feature.py settings.py migrations.py cli.py routes.py jobs.py messages.py health.py README.md api.py` 골격을 두고(필요한 것만), `feature.py` 의 `FEATURE` 한 곳에서 선언한다. 다른 파일을 고칠 필요가 없어야 하며(예외 둘: 새 설정 키는 `templates/config/*.yaml` 에 더하고, SSE 키·nav 를 더하면 `dev gen-sse-keys`·`dev gen-nav` 로 `web/src/generated/` 를 다시 만든다), 고쳐야 한다면 커널 결함으로 본다(커널을 고치거나 이슈로 올린다). 다른 기능이 쓸 공개면은 `api.py` 에만 둔다.

## 포인터

- 설계 스펙: `docs/superpowers/specs/2026-11-02-kis-trading-agent-replatform-design.md` (§2 구조, §3 마일스톤)
- 구현 플랜: `docs/superpowers/plans/` (마일스톤별 1개)
- 커널 모듈 규약: `docs/architecture.md`
- 진행 체크리스트: `docs/TODO.md`
- SDD 작업 기록(로컬 전용, gitignore): `.superpowers/sdd/<날짜>-<마일스톤>/progress.md`
