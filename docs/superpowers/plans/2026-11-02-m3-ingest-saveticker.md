# M3 — features/saveticker + features/ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 옛 저장소의 수집 계층(`src/trader/sources/` 14모듈 3,921줄)과 세이브티커 수집기(`src/trader/saveticker/` 16모듈 4,776줄 + Chrome 확장 `extensions/saveticker/`)를 **동작 보존**으로 `features/saveticker/`·`features/ingest/`(+ 작은 커널 전제) 에 옮기고, 옛 테스트(`tests/sources/*` 140, `tests/saveticker/*` 176, `tests/orchestrator/test_run_store.py` 4, `test_cli_collect.py` 6, `test_config.py` 유니버스 3, `test_daemon_news.py` 앞 4, `test_daemon_saveticker.py` 6, 확장 JS 테스트 26파일)를 새 위치로 이식해 통과시킨다. 데몬 잡 `news_collect`·`saveticker` 가 `JobSpec` 으로 등록되고, 뉴스 저장소(원장 v3 `news_items`)와 `00_inputs` 수집(`trader ingest collect`)이 CLI 로 돈다.

**Architecture:** 세 덩이, 의존 방향은 `kernel` ← `saveticker` ← `ingest`. ① 커널 전제(Task 1): 새 의존성, `paths.playwright_browsers_dir`, `bundle.app_bundle_dir` 공개, `kernel/session/run_store.py`(체크포인트 경로·봉투 — M4 가 확장), `kernel/registry/hooks.py`(훅 수집·실패 격리 실행), `DaemonDeps.secrets/registry/hooks` + `JobSpec.teardown` 호출, 테스트 공용 디스플레이 꺼짐 skip. ② `features/saveticker/`(Task 2~6): 수집기(모델·스토어·DOM/JSON 파서·네이티브 호스트·Playwright 브라우저·감독·자동화·확장 설치)·**리더**(`reader.py`, 원장을 쓰지 않는 순수 읽기)·CLI `trader saveticker …`·데몬 잡 `saveticker`(60초, 상태는 기능 모듈 전역 `_State`)·`sources.yaml` 의 `saveticker` 섹션. 커널만 import 한다. ③ `features/ingest/`(Task 7~13): `sources.yaml` 의 `kis news macro dart edgar community channels` 섹션과 `universe.yaml`·`sectors.yaml` 모델(유니버스 PIT 는 M7 scan 이지만 **종목 목록 자체는 수집 대상이라 ingest 가 소유**), 원장 v3 `news_items`, 수집기 전부, 뉴스 저장소·공시 중요도, 세이브티커 jsonl → `news_items` 적재(`saveticker_ingest.py`, 옛 리더의 `to_store_item/ingest_news`), `build_inputs/collect_and_save`, 클라이언트 조립(`clients.py`), 데몬 잡 `news_collect`(수집 → 세이브티커 적재 → prune → `news.post_ingest` 훅), CLI `trader ingest collect`. 세이브티커 데이터는 `saveticker.api` 로만 읽는다.

**Tech Stack:** Python 3.12, pydantic v2(`extra="forbid"`), httpx + respx, beautifulsoup4, pandas(지표), playwright(>=1.56,<2, 수집기 레인·테스트는 Chromium 없으면 skip), pypdf(>=6,<7), youtube-transcript-api(+requests), python-dotenv, sqlite3(JSON1), typer, Node 22 `node --test` + jsdom(확장 테스트).

**Spec:** `docs/superpowers/specs/2026-11-02-kis-trading-agent-replatform-design.md` §2.1(`features/ingest`·`features/saveticker` 행, 레인은 기능을 import 하지 않음), §2.3(`hooks` 행 `news.post_ingest`), §2.4(`news_items(novel·severity 기본)`), §2.7(합성 fixture 만, Playwright 테스트는 CI 에서 skip, Windows 매트릭스), §3 M3 행. 옛 규칙 원문: `../trading-agent/CLAUDE.md` 「수집 계층(`sources/`)」「룩어헤드 방지」「커뮤니티·뉴스 소스 정책」「수집 보강」(세이브티커 단락), `../trading-agent/docs/architecture.md` 「뉴스 저장소(Phase 19)」(13~15행) 「수집 보강」(57~59) 「세이브티커 수집기(2026-10-27)」(61~63) 「수집 계층(`sources/`)」(256~258) 「승인 지문·공시 중요도」 공시 단락(402~403) 「전체 검토 3차 — 성능」 유사 뉴스 판정(417), `../trading-agent/docs/saveticker-collection.md`. 모듈 인벤토리(세션 스크래치, 참고용): `/private/tmp/claude-501/-Users-ohhyeonseok-LLM/1a79462d-ce8a-46a0-b1c4-de0d70ffcf19/scratchpad/m3-inventory-{sources,saveticker}.md`.

## Global Constraints

- 옛 저장소는 `../trading-agent`. 옛 `secrets/`·`config/`·`state/` 는 읽지 않는다. 이 저장소 `secrets/` 도 읽지 않는다. `trader init`·CLI 실기는 scratch `TRADER_HOME` 에서만. **`trader saveticker run|service|monitor|install-browser` 는 실행하지 않는다**(사이트 로그인·Chromium 내려받기). 테스트가 띄우는 Chromium 은 `launch_or_skip`(미설치면 skip) 경로만.
- **동작 보존.** 옛 테스트의 단언을 바꾸지 않는다(import 경로·fixture·생성자·설정 객체 구성·CLI 경로만 바꾼다). 바꿔야 하는 단언은 이 플랜의 「바꾸는 옛 단언」 표에 미리 적힌 것만(없으면 추가하고 PR 본문에 적는다). 알려진 불일치는 고치지 않고 각 기능 `README.md` 「알려진 차이」에 적는다.
- import 3층: `kernel` 은 `trader.features` 를 import 하지 않는다. `features/saveticker` 는 `trader.kernel.*` 만, `features/ingest` 는 `trader.kernel.*` + `trader.features.saveticker.api` 만 import 한다(내부 모듈 금지, 함수 안 지연 import 도 금지). `registry.import_violations`·`tests/kernel/registry/test_import_boundary.py::test_real_tree_has_no_violations` 가 강제한다.
- 기능 등록은 `features/<x>/feature.py` 의 `FEATURE` 한 곳. 커널 파일 수정은 **Task 1 이 명시한 것만**(이후 Task 에서 커널을 고쳐야 하면 ledger 에 Ruling 으로 적고 최소로).
- 경로는 `platform.paths` 로만(`project_root()`·`state_dir()`·`config_dir()`), 시각 ZoneInfo 는 `config.time.KST`·`MARKET_TZ`, 저장 시각 UTC ISO8601(`+00:00`, 문자열 비교에 쓰이므로 다른 오프셋 금지), 파일명에 `:` 금지. 로거 `trader.sources`(ingest, 옛 이름 유지)·`trader.sources.disclosures`·`trader.saveticker`. 사용자 대면 메시지·docstring 한국어.
- 시크릿·계정 값은 로그·stdout·예외 메시지 어디에도 넣지 않는다(DART 키 `***` 마스킹, ECOS 키는 URL 경로라 모든 클라이언트 생성자가 `quiet_httpx()`; 세이브티커 계정 파일은 **존재 여부만** 데몬·상태가 본다). Playwright 예외는 타입 이름·단계만 기록한다.
- 공개 저장소: 수집 원문 fixture 금지(옛 `tests/sources/fixtures/*`·`saveticker/` 트리는 합성이라 그대로 복사). `templates/config/*.yaml` 은 자리표시자만(`edgar.user_agent: ""`, `channels.naver_blog: []`). 브랜딩: `community.USER_AGENT = "Mozilla/5.0 (compatible; kis-trading-agent/0.1; +https://github.com/km91hsm/kis-trading-agent)"`. 확장의 `manifest.json key`·`EXTENSION_ID = "gfchfnjghmjiidifohjglpodaakdphho"`·네이티브 호스트 이름 `HOST = "com.trader.saveticker"` 는 **바꾸지 않는다**(설치된 확장과 짝).
- 소스 on/off 기본값은 옛 값 그대로(Reddit·클리앙 on, DC·에펨코리아·토스·네이버토론 off). 사용자 결정 없이 off 소스를 켜지 않는다.
- 새 의존성은 Windows 휠이 있는 것만: `beautifulsoup4>=4.12`, `pandas>=2`, `playwright>=1.56,<2`, `pypdf>=6,<7`, `youtube-transcript-api>=1`, `requests>=2` (`uv sync` 로 lock 갱신, `uv.lock` 커밋).
- 옛 값 그대로: `MAX_PAST = 24h`, `SECTION_TIMEOUT_S = 180.0`, `MAX_STUCK_PER_SECTION = 2`, `SCHEMA_VERSION = 3`(inputs), `MACRO_MEMO_TTL = 15min`, `MAX_PENDING = 1000`, `MIN_NAME_LEN = 2`, `MIN_INTERVAL_S = 2.0`, `CACHE_DAYS = 7`, `MAP_FAIL_BACKOFF = 10min`, `MAX_LIST_PAGES = 15`, `EDGAR_LOOKBACK_DAYS = 3`, `JSONL_DAYS = 3`, `STALE_AFTER = 1h`, `SAVETICKER_WATCH_S = 60`, `STOP_GRACE_S = 70`, `LANE_RETRY_S = AUTH_RETRY_S = 6h`, `RESTART_LIMIT_RETRY = 3600`, `INSTALL_TIMEOUT_S = 900`, `LOGIN_LOCK_WAIT_S = 300`, `MAX_FRAME = 64MiB`, `MAX_SNAPSHOT = 4MiB`, `MAX_REPLY = 1MiB`, 네이티브 프레이밍 `struct "=I"`.
- 테스트는 `project`·`conn`·`tmp_path` fixture 만 쓰고 네트워크는 respx/`httpx.MockTransport` 로 막는다(YouTube 자막은 `transcript_fn` 주입). 모듈 전역 상태(`inputs._STUCK`·`_MACRO_MEMO`, `reader._schema_warned`, `clients._edgar_warned`, saveticker `job._STATE`)는 각 테스트 conftest 의 autouse fixture 가 초기화한다. 시간 단언 테스트(5초·2초)는 옛 값 그대로.
- 브랜치 `feat/m3-ingest`, PR 대상 main, 커밋 트레일러 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, 작성자는 저장소 `git config user.email`(GitHub noreply) 그대로. ruff line-length 100. CI ubuntu+windows 녹색(Chromium 없음 → 브라우저 테스트 skip). `CLAUDE.md` ≤ 16KB.

## Review Focus

스펙이 암시하지만 옛 테스트가 덮지 않는 입력. 각 줄의 테스트는 해당 Task 에 있다.

1. `sources.yaml` 에 `saveticker:` 키가 없거나 `edgar.user_agent` 가 빈 문자열이어도 설정 로드가 성공하고(기본값), `EDGAR_USER_AGENT` 시크릿·yaml 둘 다 자리표시자(`""` 또는 `@example.com` 포함)면 EDGAR 클라이언트는 `None` 이고 경고는 프로세스당 1회. → Task 7·12
2. `news_collect` 잡은 한 시장의 수집이 예외를 내도 다른 시장·세이브티커 적재·prune·훅을 끝까지 돌리고 결과 dict 에 `error` 로 남긴다; 훅 하나가 예외를 내도 잡은 성공으로 끝난다. → Task 1(hooks)·13
3. `saveticker` 잡은 데몬 `stop_event` 가 켜지면 서비스를 띄우지 않고(`{"skipped": "종료 중"}`), 데몬 종료 때 `teardown` 이 **한 번** 불려 `procs.stop_tree` 뒤 `kill_leftovers` 를 한다. → Task 1·6
4. `trader ingest collect --market US` 에 `--exchange` 가 없으면 exit 1 이고 `state/runs/` 아래에 아무 파일도 생기지 않는다(옛 `test_cli_collect_us_without_exchange_fails` 는 exit 만 봄). → Task 12
5. 세이브티커 리더는 `data/` 가 없거나 `status.json` 이 깨진 JSON 이어도 예외 없이 `snapshot() is None`·`upcoming() == []` 를 돌려주고, 보고서 발췌 경로가 `data/` 밖을 가리키면 읽지 않는다(옛 `_report_excerpt` 가드의 테스트 없음). → Task 6

---

## 파일 구조

```
pyproject.toml                       + beautifulsoup4 pandas playwright pypdf youtube-transcript-api requests
.github/workflows/ci.yml             + "Chrome 수집 확장 테스트" 단계(Linux, web npm ci 뒤)
web/package.json                     + devDependencies jsdom (확장 테스트가 ../../../web/node_modules/jsdom 를 import)
extensions/saveticker/               옛 extensions/saveticker/ 전체 사본(manifest·js·popup·README·tests/)
src/trader/kernel/
  platform/paths.py                  + playwright_browsers_dir()
  platform/bundle.py                 _app_bundle_dir → app_bundle_dir (공개)
  session/__init__.py, session/run_store.py   신규 ← orchestrator/run_store.py (84줄 그대로)
  registry/hooks.py                  신규: by_point, run
  daemon/runner.py                   DaemonDeps.secrets/registry/hooks 추가, Daemon.shutdown 이 teardown 호출
  cli/main.py                        build_daemon_deps 가 secrets·registry·hooks 를 채움
  config/sections.py                 (변경 없음 — ConfigFile 에 sources·universe·sectors 이미 있음)
src/trader/features/saveticker/
  __init__.py, README.md, api.py, feature.py
  settings.py                        SaveTickerCfg, SECTION(sources.saveticker), load_cfg
  paths.py                           home(), credentials_path()           ← saveticker/__init__.py
  models.py store.py reports.py dom.py jsonfeed.py service.py api_contract.py api_jobs.py native.py
  chromium.py browser.py supervisor.py automation.py bridge_install.py cli.py   ← 같은 이름 그대로
  reader.py                          ← sources/saveticker.py 에서 to_store_item·ingest_news 를 뺀 나머지
  job.py                             JOB(saveticker, interval 60) + _State + teardown   ← daemon._saveticker_body/_stop_saveticker
  messages.py                        saveticker_health(text)                ← notify/messages.saveticker_health
src/trader/features/ingest/
  __init__.py, README.md, api.py, feature.py
  settings.py                        KisCfg NewsCfg MacroCfg ToggleCfg EdgarCfg CommunityCfg ChannelsCfg SourcesConfig, SECTIONS(7), load_sources, DEFAULT_MARKET_QUERIES
  universe.py                        UsSymbol Universe SectorMap, SECTIONS(universe.yaml 4키·sectors.yaml 2키), load_universe, load_sectors(mtime 메모)
  migrations.py                      MIGRATIONS = (Migration(3, "ingest", …),)   news_items + 인덱스 2
  base.py news.py news_store.py disclosures.py dart.py edgar.py macro.py community.py naver_blog.py
  telegram_channel.py youtube.py manual_inputs.py indicators.py inputs.py      ← sources/* 같은 이름
  saveticker_ingest.py               to_store_item, ingest_news                 ← sources/saveticker.py 일부
  clients.py                         EDGAR_PLACEHOLDER, build_dart, build_edgar, build_input_deps   ← cli._build_dart/_build_edgar/_build_input_deps
  job.py                             JOB(news_collect) body                      ← daemon._news_body 의 수집·적재·prune 부분
  cli.py                             ingest_app: collect
templates/config/
  sources.yaml                       신규(옛 템플릿 기준, saveticker 섹션 분리·disabled_sites 삭제·자리표시자)
  universe.yaml, sectors.yaml        옛 템플릿 그대로(공개 종목 목록)
  schedule.yaml                      + news_collect: {interval_s: 1800}
tests/
  conftest.py                        + display_power_state/display_is_off/display_off + autouse _skip_browser_tests_when_display_off
  kernel/session/test_run_store.py, kernel/registry/test_hooks.py, kernel/daemon/test_runner.py(필드·teardown), kernel/platform/test_paths.py
  features/saveticker/{conftest,helpers,test_*}.py   (옛 tests/saveticker/* 19 + test_reader 7 + test_job 6 + test_feature)
  features/saveticker/fixtures/      ← tests/sources/fixtures/saveticker/ (리더 fixture)
  features/ingest/{conftest,helpers,test_*}.py        (옛 tests/sources/* + test_cli + test_universe + test_job + test_feature)
  features/ingest/fixtures/          ← tests/sources/fixtures/*.{html,xml,json,rss}
docs/architecture.md                 ## session(run_store) · ## registry 에 hooks 한 단락 · ## features 층 갱신
docs/saveticker-collection.md        ← 옛 문서(경로·명령 이름 갱신)
docs/TODO.md, CLAUDE.md              M3 체크·이연, 명령 표 2줄, 금지사항 1줄
```

옛 → 새 치환표(모든 Task 공통):

| 옛 | 새 |
|---|---|
| `from trader.sources import X` / `from trader.sources.X import …` | `from trader.features.ingest import X` / `from trader.features.ingest.X import …` (단, `sources.saveticker` → 아래) |
| `trader.sources.saveticker.{resolve_dir, available, published_at_of, markets_of, read_news_items, upcoming, snapshot, for_ticker, DEFAULT_DIR, …}` | `trader.features.saveticker.reader`(기능 밖에서는 `trader.features.saveticker.api`) |
| `trader.sources.saveticker.{to_store_item, ingest_news}` | `trader.features.ingest.saveticker_ingest` |
| `from trader.saveticker import home, credentials_path` | `from trader.features.saveticker.paths import home, credentials_path` |
| `from trader.saveticker.X import …` | `from trader.features.saveticker.X import …` |
| `trader.config.{KST, MARKET_TZ, Market, ConfigError}` | `trader.kernel.config.{KST, MARKET_TZ, Market, ConfigError}` |
| `trader.config.{NewsCfg, MacroCfg, CommunityCfg, ChannelsCfg, KisCfg, EdgarCfg, SourcesConfig, load_sources}` | `trader.features.ingest.settings` |
| `trader.config.{Universe, UsSymbol, SectorMap, load_universe, load_sectors}` | `trader.features.ingest.universe` |
| `trader.config.Secrets, load_secrets` | `trader.kernel.config.secrets` |
| `trader.logging_setup.quiet_httpx` | `trader.kernel.platform.logging_setup.quiet_httpx` |
| `trader.platform.{paths, bundle, procs, filelock}` | `trader.kernel.platform.{paths, bundle, procs, filelock}` |
| `trader.daemon.Lock` | `trader.kernel.daemon.lock.Lock` |
| `trader.broker.models.{Candle, Quote, InvestorFlow, OrderBook, drop_incomplete_tail}`, `trader.broker.errors.KisApiError` | `trader.kernel.broker.models.*`, `trader.kernel.broker.errors.KisApiError` |
| `trader.orchestrator.run_store` | `trader.kernel.session.run_store` |
| `trader.notify.messages.saveticker_health` | `trader.features.saveticker.messages.saveticker_health` |
| `NewsCfg(saveticker_dir=…, saveticker_collect=…, saveticker_retention_days=…)` | `SaveTickerCfg(dir=…, collect=…, retention_days=…)`(`trader.features.saveticker.settings`) |
| CLI `trader collect …` | `trader ingest collect …` |
| CLI `trader saveticker …` | 그대로(`Feature.cli` 가 `name=f.id` 로 마운트) |
| `from helpers_sources import …` / `from helpers_saveticker import …` / `from conftest import …` | `from tests.features.ingest.helpers import …` / `from tests.features.saveticker.helpers import …` / `from tests.conftest import …` |
| `helpers_sources.FIXTURES` | `tests/features/ingest/helpers.py::FIXTURES = Path(__file__).parent / "fixtures"`; 세이브티커 리더 fixture 는 `tests/features/saveticker/fixtures` |
| `trader.ledger.db.connect/init_schema(conn)` | 테스트는 루트 `conftest` 의 `conn`(모든 기능 마이그레이션 적용) |
| `daemon.DaemonDeps(...)`·`daemon.build_jobs`·`daemon._news_body`·`daemon._saveticker_body` | `tests/kernel/daemon/test_runner.py::make_deps` 패턴 + `jobs.build([FEATURE], …)` + `ingest.job.news_collect(ctx, job)` / `saveticker.job.watch(ctx, job)` |

**바꾸는 옛 단언(사전 승인):**

| 옛 테스트 | 바뀌는 단언 | 이유 |
|---|---|---|
| `test_saveticker_bundle.py` 템플릿 키 검사(`news.saveticker_dir` 등) | `saveticker.dir`·`saveticker.collect`·`saveticker.retention_days` | 섹션 분리 |
| `test_cli_collect.py` 의 명령 인자 `["collect", …]` | `["ingest", "collect", …]` | CLI 마운트 규칙 |
| `test_saveticker_cli.py`·`test_saveticker_bridge_install.py` 의 `parents[2]` 저장소 루트 계산 | `parents[3]`(tests/features/saveticker/ 깊이) | 레이아웃 |

이 세 종류 외에 단언을 바꿔야 한다면 implementer 는 **DONE_WITH_CONCERNS** 로 보고하고 바꾸지 않는다.

---

### Task 1: 커널 전제 — 의존성·paths·bundle·run_store·hooks·DaemonDeps·teardown·디스플레이 skip

**Files:**
- Modify: `pyproject.toml`(dependencies 6개 추가), `uv.lock`(`uv sync`), `src/trader/kernel/platform/paths.py`, `src/trader/kernel/platform/bundle.py`, `src/trader/kernel/daemon/runner.py`, `src/trader/kernel/daemon/jobs.py:79-113`(interval 잡의 `schedule_key`), `src/trader/kernel/cli/main.py:48-63`, `tests/conftest.py`, `tests/kernel/daemon/test_runner.py::test_deps_is_frozen_with_exact_field_order`, `tests/kernel/daemon/test_jobs.py`(2건 추가)
- Create: `src/trader/kernel/session/__init__.py`, `src/trader/kernel/session/run_store.py`, `src/trader/kernel/registry/hooks.py`
- Test: `tests/kernel/session/__init__.py`, `tests/kernel/session/test_run_store.py`(← `../trading-agent/tests/orchestrator/test_run_store.py` 4건), `tests/kernel/registry/test_hooks.py`, `tests/kernel/platform/test_paths.py`(추가 2건), `tests/kernel/daemon/test_runner.py`(teardown 2건 추가), `tests/test_conftest_display.py`(← `tests/saveticker/test_display_power.py` 3건)

**Interfaces:**
- Consumes: `kernel.registry.types.{Feature, Hook, JobSpec}`, `kernel.daemon.jobs.ScheduledJob`, `kernel.config.time.{KST, MARKET_TZ}`, `kernel.platform.paths.state_dir`, `kernel.platform.fsutil.atomic_replace`.
- Produces:
  - `paths.playwright_browsers_dir() -> Path` — darwin `~/Library/Caches/ms-playwright`, win32 `%LOCALAPPDATA%\ms-playwright`(없으면 `~/AppData/Local/ms-playwright`), 그 외 `~/.cache/ms-playwright`.
  - `bundle.app_bundle_dir() -> Path | None`(기존 `_app_bundle_dir` 이름 변경, 호출부 갱신).
  - `kernel/session/run_store.py`: `run_date_for(market: str, now: datetime) -> date`, `run_dir(d: date, market: str, ticker: str, state: Path | None = None) -> Path`, `market_dir(d, market, state=None, *, create=False) -> Path`, `checkpoint_payload(data, call=None) -> dict`, `write_checkpoint(dir: Path, name: str, data: dict) -> Path`(원자 쓰기), `read_checkpoint(dir, name) -> dict | None`, `list_checkpoints(dir) -> list[str]`. 옛 84줄 그대로(US 운영일 = 뉴욕 현지 날짜, `_US_OPEN = time(9, 30)`).
  - `kernel/registry/hooks.py`: `by_point(features: Iterable[Feature]) -> dict[str, tuple[Hook, ...]]`; `run(hooks: Mapping[str, tuple[Hook, ...]] | None, point: str, **kw) -> list[dict]` — 훅마다 `{"hook": fn.__module__ + "." + fn.__qualname__, "result": …}` 또는 `{"hook": …, "error": "Type: msg"}`(예외는 `logger.exception`, 다음 훅 계속). `hooks` 가 None 이면 `[]`.
  - `DaemonDeps` 필드 순서: 기존 10개 + `secrets: Any = None`, `registry: Any = None`, `hooks: Any = None`(끝에 추가). `build_daemon_deps` 가 `secrets=boot_.secrets or load_secrets()`, `registry=boot_.registry`, `hooks=hooks.by_point(boot_.features)` 로 채운다.
  - `jobs.build`: interval 잡에 `schedule_key` 가 있으면 `_lookup(schedule, key)` 값(정수, ≥ 1)을 주기로 쓰고, 키가 없거나 None 이면 `interval_s` 로 폴백, 둘 다 없으면 `ConfigError`(옛 `news_collect` 가 `schedule.yaml news_collect.interval_s` 를 읽던 규칙; "시각을 코드에 박지 않는다"). cron 규칙 불변.
  - `Daemon.shutdown` 은 스케줄러 정지 뒤 **등록된 ScheduledJob 의 `spec.teardown`** 을 spec 단위로 1회(같은 spec 이 per_market 로 여러 잡이어도 한 번) `teardown(ctx)` 호출, 예외는 `logger.exception` 후 계속. 멱등(두 번째 shutdown 은 다시 부르지 않음).
  - `tests/conftest.py`: `display_power_state(ioreg_output: str) -> int | None`, `display_is_off(state) -> bool`, `display_off() -> bool`(darwin 만 `ioreg -n IODisplayWrangler -r -d 1`, 그 외 False), autouse `_skip_browser_tests_when_display_off(request)`(파일명이 `_browser.py` 로 끝나고 꺼져 있으면 `pytest.skip`). 옛 `tests/conftest.py:70-115` 그대로.

- [ ] **Step 1: 의존성 추가 → `uv sync`** — `pyproject.toml` dependencies 에 6개(Global Constraints 버전) 추가, `uv sync` 로 lock 갱신. `uv run python -c "import bs4, pandas, playwright, pypdf, youtube_transcript_api, requests"` 성공.
- [ ] **Step 2: 실패 테스트 — paths·bundle** — `tests/kernel/platform/test_paths.py` 에 `test_playwright_browsers_dir_by_platform(monkeypatch)`(`sys.platform` 을 darwin/win32/linux 로 바꿔 세 경로 접미사 확인), `tests/kernel/platform/test_bundle.py` 에 `test_app_bundle_dir_is_public`(`hasattr(bundle, "app_bundle_dir")`). RED 확인.
- [ ] **Step 3: 구현** — `playwright_browsers_dir`, `_app_bundle_dir` → `app_bundle_dir` 이름 변경(호출부 `grep -rn _app_bundle_dir src tests`). GREEN.
- [ ] **Step 4: run_store 이식** — 옛 `tests/orchestrator/test_run_store.py` 4건을 `tests/kernel/session/test_run_store.py` 로(치환표). RED → `kernel/session/run_store.py` 옛 본문 그대로(`atomic_replace` 사용, `MARKET_TZ` 는 커널) → GREEN.
- [ ] **Step 5: hooks** — `tests/kernel/registry/test_hooks.py`: `test_by_point_groups_in_feature_order`(기능 2개·포인트 2개), `test_run_isolates_failures_and_reports_each`(첫 훅 예외·둘째 정상 → 결과 2건, 둘째 result 포함), `test_run_without_hooks_returns_empty`. RED → 구현 → GREEN.
- [ ] **Step 6: DaemonDeps 필드·teardown·interval schedule_key** — `test_jobs.py` 에 `test_interval_job_reads_seconds_from_schedule_key`(`schedule=SimpleNamespace(news_collect=SimpleNamespace(interval_s=600))`, spec `interval_s=1800, schedule_key="news_collect.interval_s"` → `trigger_kwargs == {"seconds": 600}`)·`test_interval_job_falls_back_to_interval_s_when_key_missing` 추가; `test_deps_is_frozen_with_exact_field_order` 의 목록에 `"secrets", "registry", "hooks"` 추가; `test_shutdown_calls_teardown_once_per_spec`(per_market 잡 2개가 같은 spec, teardown 호출 1회, 두 번째 shutdown 에 재호출 없음), `test_shutdown_continues_when_teardown_raises`. RED → `runner.py`·`main.py` 구현 → GREEN. `Daemon.__init__` 이 `jobs.build(...)` 결과를 `self._jobs` 로 들고 있음을 확인(없으면 추가).
- [ ] **Step 7: 디스플레이 skip** — 옛 `tests/saveticker/test_display_power.py` 3건을 `tests/test_conftest_display.py` 로(`from tests.conftest import display_power_state, display_is_off`). RED → `tests/conftest.py` 에 옛 70~115행 이식 → GREEN.
- [ ] **Step 8: 전체 확인** — `uv run pytest -q`(기존 514 + 신규 전부 통과), `uv run ruff check src tests`.
- [ ] **Step 9: Commit** — `feat(kernel): M3 전제 — 의존성·playwright_browsers_dir·run_store·hooks·DaemonDeps(secrets·registry·hooks)·teardown·디스플레이 skip`

---

### Task 2: saveticker 설정·경로·모델·스토어·보고서

**Files:**
- Create: `src/trader/features/saveticker/{__init__,settings,paths,models,store,reports}.py`, `templates/config/sources.yaml`(이 Task 는 `saveticker:` 섹션만 — 나머지 키는 Task 7 이 더함; 템플릿 로드 테스트는 Task 7 전까지 등록 섹션만 검증하므로 통과)
- Test: `tests/features/saveticker/__init__.py`, `tests/features/saveticker/conftest.py`(빈 파일 또는 공용 fixture), `tests/features/saveticker/helpers.py`(`launch_or_skip(p)` ← `tests/helpers_saveticker.py`), `tests/features/saveticker/test_settings.py`(신규 3건), `tests/features/saveticker/test_store.py`(← `test_saveticker_store.py` 18), `tests/features/saveticker/test_reports.py`(← `test_saveticker_reports.py` 5, Chromium 필요분은 `launch_or_skip`), `tests/features/saveticker/test_bundle.py` 중 `home()`·`Store.prune` 부분(← `test_saveticker_bundle.py`, 나머지 테스트는 Task 4 에서 같은 파일에 추가)

**Interfaces:**
- Consumes: `kernel.config.sections.Section`, `kernel.config.loader.{load, memo_load}`, `kernel.registry.discover`, `kernel.cli.boot.build_registry`, `kernel.platform.paths.{project_root, state_dir, config_dir}`, `kernel.config.ConfigError`.
- Produces:
  - `settings.py`: `class SaveTickerCfg(BaseModel)`(`extra="forbid"`): `dir: str | None = "state/external/saveticker"`, `collect: bool = True`, `retention_days: int = Field(30, ge=3, le=365)`; `SECTION = Section("sources", "saveticker", SaveTickerCfg, SaveTickerCfg)`; `load_cfg(path: Path | None = None, *, registry: SectionRegistry | None = None) -> SaveTickerCfg` — `memo_load("sources", registry or build_registry(discover()), path).saveticker`; 파일이 없으면 `SaveTickerCfg()`(ConfigError 삼킴 — 옛 `home()` 동작).
  - `paths.py`: `DEFAULT_DIR = "state/external/saveticker"`, `resolve_dir(configured: str | None) -> Path | None`(옛 리더의 것; 상대 경로는 `project_root()` 기준, None/"" → None), `home(cfg: SaveTickerCfg | None = None) -> Path`(cfg 없으면 `load_cfg()`; `resolve_dir(cfg.dir) or state_dir()/"external"/"saveticker"`), `credentials_path() -> Path`(`project_root()/"secrets"/"saveticker.env"`).
  - `models.py`·`store.py`·`reports.py`: 옛 모듈 그대로(`Attachment Item ViewInfo Capture`, `Store(root)`·`atomic_json`·`atomic_text`, `save_pdf`). `store.Store` 가 `reader` 와 공유하는 레이아웃(`data/feed.sqlite3`, `data/<YYYY-MM-DD>/*.jsonl`, `data/latest/<stream>.json`, `data/status.json`, `data/attachments/`)은 바꾸지 않는다.

- [ ] **Step 1: 실패 테스트 — settings** — `test_settings.py`: `test_defaults_when_sources_yaml_missing(project)`(`load_cfg()` → dir 기본·collect True·retention 30), `test_loads_saveticker_section(project)`(yaml 에 `saveticker: {dir: "x/y", retention_days: 5}` 쓰고 읽기), `test_rejects_unknown_key(project)`(`saveticker: {foo: 1}` → `ConfigError`). RED.
- [ ] **Step 2: 구현 settings·paths** → GREEN.
- [ ] **Step 3: 옛 테스트 이식 — store·reports·bundle(home·prune)** — 치환표 적용, `helpers.py` 작성. RED(모듈 없음) 확인.
- [ ] **Step 4: 모듈 이식** — `models.py`, `store.py`, `reports.py` 옛 본문 그대로(import 만 치환). GREEN(Chromium 없는 환경에서는 reports 2건 skip 이 정상).
- [ ] **Step 5: 템플릿** — `templates/config/sources.yaml` 생성, `saveticker:` 블록(3키, 기본값 주석) 만. `uv run pytest tests/kernel/config -q` 통과.
- [ ] **Step 6: Commit** — `feat(saveticker): 설정 섹션·경로·모델·스토어·보고서 이식`

---

### Task 3: saveticker 파서·플랜·API 계약·네이티브 호스트 (순수 파이썬)

**Files:**
- Create: `src/trader/features/saveticker/{dom,jsonfeed,service,api_contract,api_jobs,native}.py`
- Test: `tests/features/saveticker/test_dom.py`(← `test_saveticker_dom.py` 21), `test_json.py`(← `test_saveticker_json.py` 11), `test_api_contract.py`(← 7), `test_api_enrichment.py`(← 8), `test_native.py`(← `test_saveticker_native.py` 26), `test_service.py` 의 **순수 부분**(`build_views`·`accumulate`·`choose_due`·`credentials` — 브라우저 부분은 Task 4 가 같은 파일에 추가)

**Interfaces:**
- Consumes: Task 2 `models`·`store`, `kernel.daemon.lock.Lock`(native `serve` 의 `runtime/bridge-host.lock`·`service.lock`), `kernel.config.KST`.
- Produces: 옛 공개 이름 그대로 — `dom.{text, identity, tickers, authentication_required, client_error_visible, months_between, extract, news_detail, comments, calendar_month, news_sources}`, `jsonfeed.{ROUTES, route, body_text, aware, scalar, news_item, extract}`, `service.{PATHS, FatalCollectionError, View, lane_for, build_views, accumulate, collect_cycle, choose_due}`, `api_contract.{ORIGIN, MAX_BYTES, INDEX_SYMBOLS, descriptor, parse_snapshot, validate_response}`, `api_jobs.{ApiError, BrowserApiSession, …}`, `native.{EXTENSION_ID, ORIGIN, LANES, STATES, REASONS, build_plan, InvalidMessage, NativeSession, serve}`. `native.serve(root, extension_id, origin, stdin=None, stdout=None)` 시그니처 불변.

- [ ] **Step 1: 옛 테스트 이식(6파일)** — 치환표 적용, `test_service.py` 는 `pytest.importorskip("playwright.sync_api")`·`launch_or_skip` 쓰는 함수를 **제외**하고 옮긴다(Task 4 가 추가). RED.
- [ ] **Step 2: 모듈 이식** — 옛 본문 그대로, `trader.daemon.Lock` 지연 import 는 `from trader.kernel.daemon.lock import Lock` 모듈 상단 import 로. GREEN.
- [ ] **Step 3: ruff** — `uv run ruff check src/trader/features/saveticker tests/features/saveticker`.
- [ ] **Step 4: Commit** — `feat(saveticker): DOM·JSON 파서·수집 플랜·API 계약·네이티브 호스트 이식`

---

### Task 4: saveticker Chromium·브라우저·감독·자동화

**Files:**
- Create: `src/trader/features/saveticker/{chromium,browser,supervisor,automation}.py`
- Test: `tests/features/saveticker/test_automation.py`(← `test_saveticker_automation.py` 14), `test_supervisor.py`(← 1), `test_service.py` 에 브라우저 부분 추가(← 나머지 ~9, `launch_or_skip`), `test_bundle.py` 에 `chromium.*`·`automation.kill_leftovers/should_start/service_elsewhere` 부분 추가(← `test_saveticker_bundle.py` 나머지; `test_service_elsewhere_reads_process_list` 는 옛대로 `skipif(os.name == "nt")`)

**Interfaces:**
- Consumes: `kernel.platform.paths.playwright_browsers_dir`(Task 1), `kernel.platform.procs.{pid_alive, process_start, kill_group}`, `kernel.platform.bundle.cli_argv`, `kernel.daemon.lock.Lock`, Task 2 `store.atomic_json`, Task 3 `service`, `kernel.config.ConfigError`.
- Produces: 옛 공개 이름 그대로 — `chromium.{INSTALL_TIMEOUT_S, prepare_env, installed, install, ensure}`, `browser.{SiteClientError, AccessDenied, LoginRequired, VerificationRequired, VERIFY_TEXTS, credentials, Browser}`, `supervisor.{WorkerExited, supervise}`, `automation.{RUNNERS, STREAMS, LABELS, LANES, AUTH_ERRORS, LANE_BLOCK_FILE, LANE_RETRY_S, AUTH_RETRY_S, STOP_GRACE_S, RESTART_LIMIT_RETRY, read, collector_alive, evaluate, notify, monitor, classify_exit, lane_blocks, active_skip_lanes, clear_blocks, status, retry_allowed, kill_leftovers, service_elsewhere, should_start, service, plists}`. 테스트가 모듈 속성을 monkeypatch 하므로(`automation.service_elsewhere`, `chromium.install`, `bundle.cli_argv`) **모듈 안에서 같은 모듈의 함수를 모듈 속성으로 호출**하는 옛 구조를 유지한다.
- `automation.service(root)` 가 띄우는 자식 argv 는 `[*bundle.cli_argv(), "saveticker", "run", "--headed", "--max-scrolls", "2", "--max-details", "2", ("--skip-lanes", csv)]` 그대로(`trader saveticker run` 경로가 Task 6 CLI 와 일치해야 한다).

- [ ] **Step 1: 옛 테스트 이식(4파일)** — RED.
- [ ] **Step 2: 모듈 이식** — 옛 본문 그대로. `chromium.prepare_env` 는 커널 `paths.playwright_browsers_dir()`. `automation.collector_alive` 의 `ps` 명령 문자열(`trader saveticker run`·`native-host`·확장 origin)·`service_elsewhere` 의 `"trader saveticker service"` 검색어 불변. GREEN(Chromium 없으면 service 브라우저 테스트 skip).
- [ ] **Step 3: Windows 분기 확인** — `grep -n "os.name" src/trader/features/saveticker/*.py` 로 옛 분기(`nt`)가 전부 남아 있는지 보고 report 에 적는다.
- [ ] **Step 4: Commit** — `feat(saveticker): Chromium 설치·브라우저 수집기·감독·서비스 자동화 이식`

---

### Task 5: saveticker 확장 설치 + `extensions/saveticker/` + JS 테스트 CI

**Files:**
- Create: `src/trader/features/saveticker/bridge_install.py`, `extensions/saveticker/`(옛 폴더 전체 사본: `manifest.json core.js worker.js content.js api-content.js api-runner.js pdf-main.js html-refresh.js recheck.js minimized.js recovery-health.js popup.{html,css,js} package.json README.md tests/`)
- Modify: `web/package.json`(devDependencies `"jsdom": "^26"` — `cd web && npm install -D jsdom` 로 `package-lock.json` 갱신), `.github/workflows/ci.yml`
- Test: `tests/features/saveticker/test_bridge_install.py`(← `test_saveticker_bridge_install.py` 14; `parents[2]` → `parents[3]`), JS: `cd extensions/saveticker && node --test tests/*.test.js`

**Interfaces:**
- Consumes: `kernel.platform.bundle.{resource_dir, is_frozen, cli_executable, app_bundle_dir}`, `kernel.platform.paths.project_root`, `kernel.config.loader.load`(`collector_root` 가 `project_home/config/sources.yaml` 을 명시 경로로 읽음 — `load("sources", build_registry(discover()), path)` 뒤 `.saveticker.dir`), Task 2 `store.{atomic_json, atomic_text}`·`paths.home`, Task 4 `automation.{kill_leftovers, read}`, Task 3 `native`.
- Produces: `bridge_install.{HOST, EXTENSION_ID, PERMISSIONS, InstallError, extension_source, collector_root, prepare, registration_path, install_host, uninstall_host, native_entry, is_native_origin}`. `extension_source()` 후보: `bundle.resource_dir()/"extensions"/"saveticker"`, 비번들이면 `Path(__file__).resolve().parents[4] / "extensions" / "saveticker"`(`src/trader/features/saveticker/bridge_install.py` → 저장소 루트는 parents[4]; 구현 전에 `python -c` 로 확인해 report 에 적는다).
- CI: `ci.yml` 의 웹 단계 뒤에 Linux 전용 단계 `Chrome 수집 확장 테스트`: `node --test extensions/saveticker/tests/*.test.js`(cwd 저장소 루트; jsdom 은 `web/node_modules` 에서 `../../../web/node_modules/jsdom/lib/api.js` 상대 경로로 import 되므로 `web` 의 `npm ci` 가 먼저여야 한다).

- [ ] **Step 1: 확장 사본** — `cp -R ../trading-agent/extensions/saveticker extensions/`(`node_modules`·lock 없음 확인). `extensions/saveticker/README.md` 의 옛 명령 경로(`trader saveticker extension …`)는 그대로 유효.
- [ ] **Step 2: jsdom** — `cd web && npm install -D jsdom && npm run typecheck && npm test -- --run`(웹 변경 없음 확인). 저장소 루트에서 `node --test extensions/saveticker/tests/*.test.js` 전부 통과.
- [ ] **Step 3: 옛 테스트 이식 — bridge_install** — 치환표 + `parents` 깊이 조정, `bundle.app_bundle_dir` monkeypatch 가 공개 이름을 쓰는지 확인. RED.
- [ ] **Step 4: 모듈 이식** → GREEN(Windows 레지스트리 테스트는 옛 플랫폼 가드 그대로).
- [ ] **Step 5: CI 단계 추가** — `ci.yml` 수정(위 명세).
- [ ] **Step 6: Commit** — `feat(saveticker): Chrome 확장(extensions/saveticker)·네이티브 호스트 설치·JS 테스트 CI`

---

### Task 6: saveticker 리더·CLI·데몬 잡·feature·api·README

**Files:**
- Create: `src/trader/features/saveticker/{reader,cli,job,messages,api,feature}.py`, `src/trader/features/saveticker/README.md`
- Test: `tests/features/saveticker/test_reader.py`(← `tests/sources/test_saveticker.py` 중 `ingest_news` 1건을 뺀 7건), `tests/features/saveticker/fixtures/`(← `tests/sources/fixtures/saveticker/` 트리 전체), `test_cli.py`(← `test_saveticker_cli.py` 8; `from trader.kernel.cli.main import app`), `test_job.py`(← `test_daemon_saveticker.py` 6, 새 JobSpec 으로 재작성), `test_feature.py`(신규 3), `test_public_api.py`(신규 1)

**Interfaces:**
- Consumes: Task 1 `DaemonDeps`(`ctx.stop_event, ctx.now, ctx.notifier, ctx.registry`), `kernel.platform.procs.{spawn_detached, stop_tree}`, `kernel.platform.bundle.cli_argv`, `kernel.notify.messages.event`, `kernel.registry.types.{Feature, JobSpec}`.
- Produces:
  - `reader.py`(← `sources/saveticker.py` 에서 `to_store_item`·`ingest_news` 제거, `resolve_dir`·`DEFAULT_DIR` 는 `paths` 에서 re-export): `NEWS_STREAMS, REPORT_EXCERPT_CHARS, SUMMARY_CHARS, JSONL_DAYS, CALENDAR_BACK_DAYS, CALENDAR_AHEAD_DAYS, STALE_AFTER, STALE_AFTER_BY_PREFIX, SCHEMA_VERSION, available(root) -> bool, published_at_of(item) -> str | None, markets_of(item) -> list[str], read_news_items(root, *, today: date, days=JSONL_DAYS) -> list[dict], report_excerpt(root, item) -> str | None`(옛 `_report_excerpt` 공개 — ingest 의 `to_store_item` 이 쓴다), `upcoming(root, *, asof, days=7) -> list[dict]`, `snapshot(root, *, asof) -> dict | None`, `for_ticker(snap, ticker) -> dict | None`. 모듈 전역 `_schema_warned` 유지.
  - `messages.py`: `saveticker_health(text: str) -> str` = `messages.event("세이브티커 수집", text)`.
  - `job.py`: `SAVETICKER_WATCH_S = 60`; `@dataclass class _State: proc: Any = None; home: Path | None = None; pruned: date | None = None`; 모듈 전역 `_STATE = _State()`; `reset_state()`(테스트용); `watch(ctx, job) -> dict`(← `daemon._saveticker_body`: `cfg = load_cfg(registry=ctx.registry)`; `not cfg.collect or resolve_dir(cfg.dir) is None` → `{"skipped": "꺼짐"}`; `ctx.stop_event.is_set()` → `{"skipped": "종료 중"}`; 살아 있으면 `service="실행 중"`, 아니면 `automation.should_start(home, credentials_path(), now=ctx.now())` → 시작 시 `automation.kill_leftovers(home)` + `procs.spawn_detached([*bundle.cli_argv(), "saveticker", "service"], log=home/"runtime"/"daemon-service.log", env={**os.environ, "TRADER_HOME": str(project_root()), "PYTHONIOENCODING": "utf-8"})`; 사유가 "계정 파일 없음" 이 아니면 `automation.monitor(home, send=lambda t: ctx.notifier.send(saveticker_health(t)))` → `health`; KST 오늘 1회 `Store(home/"data").prune(today=, keep_days=cfg.retention_days)` → `pruned`); `stop(ctx) -> None`(← `_stop_saveticker`: `procs.stop_tree(proc, grace=automation.STOP_GRACE_S)` → `automation.kill_leftovers(home)`, 예외는 `logger.exception`); `JOB = JobSpec(id="saveticker", kind="saveticker", trigger="interval", per_market=False, schedule_key=None, interval_s=SAVETICKER_WATCH_S, enabled=None, gate="none", heavy=False, misfire="default", catch_up="none", start_soon_s=0, body=watch, teardown=stop)`.
  - `cli.py`: 옛 `saveticker/cli.py` 그대로 — `app = typer.Typer(name="saveticker", help=…, no_args_is_help=True)`: `status`, `import-capture`, `query`, `export`, `run`, `install-browser`, `service`, `monitor`, 하위 `extension prepare|install-host|uninstall-host|status`, 숨김 `native-host ORIGIN`. 테스트가 import 하는 `root(), runtime(), sqlite_diagnostics, rolling_range, select_views, plan_lanes, serialized_login, LOGIN_LOCK, LOGIN_LOCK_WAIT_S` 유지.
  - `api.py`: `SaveTickerCfg, load_cfg, DEFAULT_DIR, resolve_dir, home, credentials_path, available, published_at_of, markets_of, read_news_items, report_excerpt, upcoming, snapshot, for_ticker, JSONL_DAYS, NEWS_STREAMS, SCHEMA_VERSION`(ingest 가 쓸 것 전부).
  - `feature.py`: `FEATURE = Feature(id="saveticker", tier="service", sections=(SECTION,), jobs=(JOB,), cli=app)`.

- [ ] **Step 1: 옛 테스트 이식 — reader·cli** — fixture 트리 복사(`cp -R ../trading-agent/tests/sources/fixtures/saveticker tests/features/saveticker/fixtures/saveticker`), 치환표. `test_reader.py` 에 Review Focus 5 테스트 추가: `test_snapshot_and_upcoming_tolerate_missing_or_broken_status(tmp_path)`(data 없음 → None/[], `status.json` 에 `{` 만 → None/[]), `test_report_excerpt_refuses_path_outside_data(tmp_path)`(`attachments/../../x.txt` → None). RED.
- [ ] **Step 2: reader·cli·messages 이식** → GREEN.
- [ ] **Step 3: job 테스트 재작성** — `test_job.py` 6건: `make_deps` 패턴(`tests/kernel/daemon/test_runner.py` 참고)으로 `DaemonDeps(registry=<SaveTickerCfg 를 등록한 레지스트리>, notifier=Sent())`; `monkeypatch` 로 `job.procs.spawn_detached/stop_tree`, `automation.service_elsewhere/monitor`, `store.Store.prune`; 옛 단언 유지(argv 끝 `["saveticker","service"]`, env `TRADER_HOME`, 로그 경로 `runtime/daemon-service.log`, 알림 `"[세이브티커 수집]"` 접두, 하루 1회 prune, `test_job_registered` 는 `jobs.build([FEATURE], schedule=…, settings=…, markets=["KR"], ctx=…)` 결과에 id `saveticker`·interval 60). Review Focus 3: `test_stop_event_skips_start_and_teardown_runs_once`. autouse fixture 가 `reset_state()`. RED → `job.py` → GREEN.
- [ ] **Step 4: feature·api·README** — `test_feature.py`: `test_feature_discovered_and_contract_clean`(`check(discover()) == []`, id 목록에 `saveticker`), `test_section_registered_in_sources_file`, `test_cli_mounted_as_saveticker`(CliRunner `["saveticker", "--help"]` exit 0). `test_public_api.py`: `api.__all__` 의 이름이 전부 import 되고 `reader`·`paths` 외 내부 모듈 이름이 없는지. README 는 accounts README 와 같은 절 구성(책임·공개면·규약·테스트·알려진 차이). 규약에는 옛 architecture 「세이브티커 수집기」 단락의 규칙(헤드리스 403·보안 확인·CAPTCHA 우회 금지, `runner*.json` 에는 단계·오류 타입만, 작업 4개 각자 브라우저, 작업 하나의 AccessDenied 는 그 작업만 6시간, 전체 정지도 6시간마다 재시도, 재확인 화면은 `verification` 으로 정지, `login.lock` 직렬 로그인, `service.lock` 공유, 리더 stale 규칙 1h/하루/2주)을 옮긴다.
- [ ] **Step 5: `uv run pytest -q`·ruff** — 전체 녹색, `import_violations` 0.
- [ ] **Step 6: Commit** — `feat(saveticker): 리더·CLI·데몬 잡·FEATURE·api·README`

---

### Task 7: ingest 설정(sources·universe·sectors)·템플릿·원장 v3·base

**Files:**
- Create: `src/trader/features/ingest/{__init__,settings,universe,migrations,base,feature,api}.py`(feature·api 는 골격, Task 13 이 완성), `templates/config/universe.yaml`, `templates/config/sectors.yaml`
- Modify: `templates/config/sources.yaml`(7키 추가), `templates/config/schedule.yaml`(`news_collect: {interval_s: 1800}`)
- Test: `tests/features/ingest/__init__.py`, `conftest.py`(autouse 전역 초기화 — Task 12 가 채움), `helpers.py`(← `tests/helpers_sources.py` 251줄: `FIXTURES, T0, load, load_bytes, corpcode_zip, make_candles, FakeMarket, FakeDart, FakeEdgar`), `fixtures/`(← `tests/sources/fixtures/*` 파일들, saveticker 트리 제외), `test_settings.py`(신규 5), `test_universe.py`(← `test_config.py` 유니버스 3 + 신규 1), `test_migrations.py`(신규 2), `test_base.py`(← 5)

**Interfaces:**
- Consumes: `kernel.config.sections.Section`, `kernel.config.loader.{load, memo_load}`, `kernel.ledger.migrations.Migration`, `kernel.registry.discover`, `kernel.cli.boot.build_registry`, `kernel.platform.paths.config_dir`.
- Produces:
  - `settings.py`: 옛 모델 그대로(`KisCfg NewsCfg MacroCfg ToggleCfg EdgarCfg CommunityCfg ChannelsCfg`, `DEFAULT_MARKET_QUERIES`) **단** `NewsCfg` 에서 `saveticker_dir/saveticker_collect/saveticker_retention_days` 제거, `EdgarCfg.user_agent: str = ""`. `class SourcesConfig(BaseModel)`(`extra="forbid"`, 7필드, 전부 기본값 — `edgar: EdgarCfg = Field(default_factory=EdgarCfg)`). `SECTIONS: tuple[Section, ...]` = 7개 `Section("sources", <key>, <Cfg>, <Cfg>)`. `load_sources(path: Path | None = None, *, registry: SectionRegistry | None = None) -> SourcesConfig` — `m = memo_load("sources", registry or build_registry(discover()), path)` 뒤 `SourcesConfig(**{k: getattr(m, k) for k in SourcesConfig.model_fields})`(다른 기능의 키는 무시).
  - `universe.py`: `UsSymbol(symbol, exchange: Literal["NAS","NYS","AMS"])`, `Universe(kr, us, exclude, watch; frozen)`, `SectorMap`(옛 `config.py:903~` 그대로: `sectors, kr, us, kr_industry_to_sector, us_industry_to_sector` + 교차 검증 `_declared` + `sector_of`, frozen). 커널 섹션 등록은 템플릿 로드·키 검증용으로 키별: `UNIVERSE_SECTIONS` = `Section("universe", k, t, list)` for `kr: list[str]`, `us: list[UsSymbol]`, `exclude: list[str]`, `watch: list[str]`; `SECTOR_SECTIONS` = `Section("sectors", k, t, <default>)` for `sectors: list[str]`, `kr: dict[str,str]`, `us: dict[str,str]`, `kr_industry_to_sector: dict[str,str]`, `us_industry_to_sector: dict[str,str]`. **교차 필드 검증은 `load_universe(path=None) -> Universe`·`load_sectors(path=None) -> SectorMap` 이 전체 모델로 직접 한다**(yaml 읽기 → `Universe/SectorMap.model_validate`, 옛 `_memo_yaml` 의 경로·mtime_ns·크기 메모, 검증 오류는 메모 안 함, 오류는 `ConfigError`).
  - `migrations.py`: `MIGRATIONS = (Migration(3, "ingest", (<CREATE TABLE news_items … novel INTEGER, severity TEXT 포함 최종 스키마>, <idx_news_market_pub>, <ix_news_market_received>), None, "ingest"),)`(`Migration` 생성자 필드 순서는 `kernel/ledger/migrations.py` 확인).
  - `base.py`: 옛 83줄 그대로(`Section, MAX_PAST, SKIP_REASONS, TAG_RE, utc_now, strip_tags, run_section, skipped_section, owned_client`).
  - `feature.py` 골격: `FEATURE = Feature(id="ingest", tier="service", sections=(*SECTIONS, *UNIVERSE_SECTIONS, *SECTOR_SECTIONS), migrations=MIGRATIONS)` — Task 13 이 jobs·cli 를 더한다.

- [ ] **Step 1: 실패 테스트 — settings·universe·migrations** — `test_settings.py`: `test_sources_template_loads_and_matches_defaults(project)`(템플릿 복사 후 `load_sources()` == `SourcesConfig()` 단, `channels.naver_blog == []`), `test_load_sources_ignores_other_features_keys(project)`(yaml 에 `saveticker:` 있어도 성공), `test_edgar_user_agent_optional`(`SourcesConfig.model_validate({})` 성공), `test_news_cfg_rejects_old_saveticker_keys`(`NewsCfg(saveticker_dir="x")` → ValidationError), `test_market_queries_default_counts`(KR 7·US 5). `test_universe.py`: 옛 3건(치환표; `write_yaml` 헬퍼는 `tests/features/ingest/helpers.py` 에) + `test_universe_template_loads`. `test_migrations.py`: `test_news_items_schema_columns(conn)`(14 열 이름), `test_version_is_three(conn)`(`current_version(conn) == 3`). RED.
- [ ] **Step 2: 구현 settings·universe·migrations·feature 골격·템플릿** — `templates/config/sources.yaml` 에 7키(옛 템플릿 값, `edgar.user_agent: ""` + 주석 "`secrets/.env` 의 `EDGAR_USER_AGENT` 를 쓴다", `channels.naver_blog: []`, `disabled_sites` 없음), `universe.yaml`·`sectors.yaml` 은 옛 템플릿 사본, `schedule.yaml` 에 `news_collect`. GREEN. `tests/kernel/config/test_templates.py` 통과. `tests/kernel/cli/test_selfcheck.py::test_selfcheck_json_has_version_and_features` 의 `data["features"] == ["accounts"]` 를 `["accounts", "ingest", "saveticker"]` 로(기능 추가에 따른 설정 변경, 단언 의미 불변 — 발견 순서는 `discover()` 정렬 규칙 확인).
- [ ] **Step 3: base 이식** — `test_base.py` 5건 → RED → `base.py` → GREEN. `helpers.py`·`fixtures/` 복사(`FIXTURES = Path(__file__).parent / "fixtures"`).
- [ ] **Step 4: Commit** — `feat(ingest): sources·universe·sectors 설정 섹션·템플릿·원장 v3 news_items·base`

---

### Task 8: ingest 뉴스·뉴스 저장소·공시 중요도

**Files:**
- Create: `src/trader/features/ingest/{news,news_store,disclosures}.py`
- Test: `tests/features/ingest/test_news.py`(← 9), `test_news_store.py`(← 9), `test_disclosures.py`(← 8)

**Interfaces:**
- Consumes: Task 7 `settings.NewsCfg`, `universe.Universe/UsSymbol`, `base`, `kernel.config.secrets.Secrets`, `kernel.config.{KST, Market}`.
- Produces: 옛 공개 이름 그대로 — `news.{GOOGLE_RSS, NAVER_NEWS, NAVER_HEADER_ID, NAVER_HEADER_SECRET, MACRO_QUERIES, GoogleNewsRss, NaverNews, collect_news}`, `news_store.{MIN_NAME_LEN, MAX_PENDING, url_hash, tag, universe_names, insert_items, collect, row_to_dict, recent, normalize_title, title_tokens, is_similar, novel_items, prune, count}`(옛 `_row` 를 `row_to_dict` 로 공개하고 `_row = row_to_dict` 별칭 유지), `disclosures.{SEVERITIES, DART_CRITICAL, DART_HIGH, DART_NEGATE, EDGAR_ITEMS, EDGAR_LOOKBACK_DAYS, classify_dart, classify_8k, collect, critical_recent}`. `disclosures` 의 `news_store` 지연 import 는 모듈 상단 import 로.

- [ ] **Step 1: 옛 테스트 이식(3파일)** — `Universe(kr=…, us=[UsSymbol(…)])` 는 `ingest.universe` 에서. RED.
- [ ] **Step 2: 모듈 이식** → GREEN(`test_novel_items_large_untagged_pool_is_fast` < 5초 포함).
- [ ] **Step 3: Commit** — `feat(ingest): 뉴스 수집·뉴스 저장소·공시 중요도 이식`

---

### Task 9: ingest DART·EDGAR·거시

**Files:**
- Create: `src/trader/features/ingest/{dart,edgar,macro}.py`
- Test: `tests/features/ingest/test_dart.py`(← 15), `test_edgar.py`(← 12), `test_macro.py`(← 9)

**Interfaces:**
- Consumes: `kernel.platform.logging_setup.quiet_httpx`, `kernel.config.KST`, Task 7 `settings.MacroCfg`·`base`.
- Produces: `dart.{BASE, CACHE_DAYS, MAP_FAIL_BACKOFF, ANNUAL, RATIO_KEYS, MAX_LIST_PAGES, DartError, fiscal_year_for, DartClient, basic_ratios, ratios_kr}`, `edgar.{TICKERS_URL, FACTS_URL, SUBMISSIONS_URL, EdgarError, EdgarClient, financial_history_us, financials_us, ratios_by_year_us, ratios_from_facts, ratios_us}`, `macro.{FRED_URL, ECOS_URL, FRED_SERIES, ECOS_SERIES, Fred, Ecos, collect_macro}`. 전부 옛 본문 그대로.

- [ ] **Step 1: 옛 테스트 이식(3파일)** — RED.
- [ ] **Step 2: 모듈 이식** → GREEN.
- [ ] **Step 3: Commit** — `feat(ingest): DART·EDGAR·거시(FRED·ECOS) 수집기 이식`

---

### Task 10: ingest 커뮤니티·채널(네이버 블로그·텔레그램·YouTube)

**Files:**
- Create: `src/trader/features/ingest/{community,naver_blog,telegram_channel,youtube}.py`
- Test: `tests/features/ingest/test_community.py`(← 11), `test_naver_blog.py`(← 4), `test_telegram_channel.py`(← 2), `test_youtube.py`(← 4)

**Interfaces:**
- Consumes: Task 7 `settings.{CommunityCfg, ChannelsCfg}`·`base.{owned_client, strip_tags, utc_now}`, `kernel.config.{KST, Market}`.
- Produces: `community.{USER_AGENT(새 브랜딩 문자열), MIN_INTERVAL_S, iso_to_utc, kst_to_iso, matches, Reddit, Clien, DcInside, FmKorea, collect_community, collect_channels}` — 옛 `_iso_to_utc/_kst_to_iso/_matches` 를 공개 이름으로 바꾸고 `_iso_to_utc = iso_to_utc` 등 별칭 유지(옛 테스트가 `_matches` 를 import). `collect_channels` 의 지연 import(naver_blog·telegram_channel·youtube)는 **유지**(순환: 세 모듈이 community 를 import). `naver_blog.{RSS_URL, EXCERPT_CHARS, NaverBlogSource}`, `telegram_channel.{PREVIEW_URL, TelegramChannelSource}`, `youtube.{RSS_URL, EXCERPT_CHARS, TRANSCRIPT_TIMEOUT_S, default_transcript, YouTubeSource}`.

- [ ] **Step 1: 옛 테스트 이식(4파일)** — RED.
- [ ] **Step 2: 모듈 이식** → GREEN. `USER_AGENT` 는 Global Constraints 문자열.
- [ ] **Step 3: Commit** — `feat(ingest): 커뮤니티·네이버 블로그·텔레그램 채널·YouTube 수집기 이식`

---

### Task 11: ingest 수동 입력·기술 지표·세이브티커 적재

**Files:**
- Create: `src/trader/features/ingest/{manual_inputs,indicators,saveticker_ingest}.py`
- Test: `tests/features/ingest/test_manual_inputs.py`(← 3), `test_indicators.py`(← 6), `test_saveticker_ingest.py`(← `tests/sources/test_saveticker.py::test_ingest_news_is_idempotent_and_splits_markets` 1 + 신규 1)

**Interfaces:**
- Consumes: `kernel.platform.paths.state_dir`, `kernel.broker.models.Candle`, Task 8 `news_store.insert_items`, Task 6 `trader.features.saveticker.api.{read_news_items, report_excerpt, published_at_of, markets_of, available, NEWS_STREAMS, SUMMARY_CHARS, JSONL_DAYS}`.
- Produces: `manual_inputs.{MAX_MARKET_NOTES, read, append_market, read_market}`, `indicators.{COLUMNS, to_frame, indicator_frame, latest}`, `saveticker_ingest.{to_store_item(root, item) -> dict, ingest_news(conn, root, *, now=…, names_by_market=None, days=JSONL_DAYS) -> dict}`(옛 본문 그대로; `source=f"saveticker:{publisher}"`, `query=<stream>`, `lang="ko"`, `extra_tickers=True`, 폴더 없음 → `{"skipped": "saveticker 폴더 없음"}`).

- [ ] **Step 1: 옛 테스트 이식** — `test_saveticker_ingest.py` 는 fixture 를 `tests/features/saveticker/fixtures/saveticker` 에서 복사(`shutil.copytree`); 신규 `test_ingest_news_skips_when_root_missing(conn, tmp_path)`. RED.
- [ ] **Step 2: 모듈 이식** → GREEN. `import_violations` 가 `saveticker.api` 경유만 허용하는지 `uv run pytest tests/kernel/registry -q`.
- [ ] **Step 3: Commit** — `feat(ingest): 수동 입력·기술 지표·세이브티커 jsonl 적재 이식`

---

### Task 12: ingest `build_inputs`·클라이언트 조립·CLI `ingest collect`

**Files:**
- Create: `src/trader/features/ingest/{inputs,clients,cli}.py`
- Modify: `tests/features/ingest/conftest.py`(autouse 초기화)
- Test: `tests/features/ingest/test_inputs.py`(← 35 중 `test_fundamental_peer_*` 3건 제외 32), `test_clients.py`(신규 4), `test_cli.py`(← `tests/test_cli_collect.py` 6 + Review Focus 4 신규 1)

**Interfaces:**
- Consumes: `kernel.session.run_store.{read_checkpoint, run_date_for, run_dir, write_checkpoint}`, `kernel.broker.models.drop_incomplete_tail`, `kernel.config.{KST, MARKET_TZ, ConfigError, Market}`, `kernel.config.secrets.{Secrets, load_secrets}`, `kernel.cli.boot.boot`, `kernel.platform.paths.state_dir`, `trader.features.saveticker.api.{resolve_dir, available, snapshot, for_ticker, upcoming}`, Task 7~11 모듈 전부.
- Produces:
  - `inputs.py`: 옛 본문 그대로 + 두 변경 — (a) `InputDeps` 에 `peer_fn: Callable[..., dict] | None = None` 추가, `_peer_section` 은 `peer_fn` 이 None 이면 `skipped_section("no sector peers")`(피처 스토어는 M7; `peer_fn(market=, ticker=, asof_date=, sectors=) -> dict` 계약만 둔다), (b) `_saveticker_section` 은 `saveticker.api` 로. 공개: `SECTION_TIMEOUT_S, MAX_STUCK_PER_SECTION, SCHEMA_VERSION, MAX_PAST, MAX_FUTURE, MACRO_MEMO_TTL, InputDeps, build_inputs, collect_and_save, jsonable`(옛 `_jsonable` 공개 + 별칭), `reset_memo()`(테스트용: `_STUCK`·`_MACRO_MEMO` 비움). 세이브티커 설정은 `saveticker.api.load_cfg(registry=)` 로 읽으므로 `build_inputs(..., saveticker_cfg: Any | None = None)` 키워드를 더하고 None 이면 `skipped_section("no saveticker dir")`.
  - `clients.py`: `EDGAR_PLACEHOLDER = "@example.com"`; `build_dart(secrets, cache: Path | None = None) -> DartClient | None`; `build_edgar(cfg: SourcesConfig, secrets, cache=None) -> EdgarClient | None`(`secrets.EDGAR_USER_AGENT` 우선 → yaml `edgar.user_agent`; 둘 다 빈 문자열이거나 `EDGAR_PLACEHOLDER` 포함 또는 `edgar.enabled=False` 면 None, 자리표시자 경고는 프로세스당 1회 `_edgar_warned`); `build_input_deps(secrets, cfg, *, market=None, conn_factory=None) -> InputDeps`(옛 `cli._build_input_deps`; `market` 은 호출자가 `accounts.api` 로 만든 시세 클라이언트 — ingest 는 accounts 를 import 하지 않으므로 **인자로 받는다**, 없으면 None 으로 시세 절 건너뜀 → README 알려진 차이).
  - `cli.py`: `ingest_app = typer.Typer(name="ingest", help="수집", no_args_is_help=True)`; `collect(--ticker, --market KR, --exchange, --asof, --force)` 옛 출력 문구 그대로(`저장: …`/`건너뜀(이미 존재, --force 로 덮어씀): …`/`  - <오류>`/`오류 절 N건`, 설정·인자 오류 exit 1). 시세 클라이언트: M3 에서는 `None`(계좌 조립은 accounts 소유 — README 알려진 차이, M4 가 레인에서 주입).

- [ ] **Step 1: 옛 테스트 이식 — inputs** — 32건(peer 3건 제외), `conftest.py` autouse `_reset_ingest_state` 가 `inputs.reset_memo()` 호출. `test_sentiment_saveticker_section_from_local_dir` 는 `saveticker_cfg=SaveTickerCfg(dir=str(root))` 로 호출(설정 객체 구성 변경, 단언 불변). RED.
- [ ] **Step 2: inputs 이식** → GREEN(2초·동시성 단언 포함).
- [ ] **Step 3: clients 테스트·구현** — `test_clients.py`: `test_build_edgar_prefers_secret_over_yaml`, `test_build_edgar_placeholder_disables_and_warns_once(caplog)`(Review Focus 1), `test_build_dart_none_without_key`, `test_build_input_deps_without_market`. RED → 구현 → GREEN.
- [ ] **Step 4: CLI 이식** — 옛 6건(`["ingest","collect",…]`, `quiet_project` fixture 는 `project` 로) + `test_cli_collect_us_without_exchange_writes_nothing(project)`. RED → `cli.py` → GREEN.
- [ ] **Step 5: Commit** — `feat(ingest): build_inputs·collect_and_save·클라이언트 조립·trader ingest collect`

---

### Task 13: ingest 데몬 잡 `news_collect`·api·feature 완성·README

**Files:**
- Create: `src/trader/features/ingest/job.py`, `src/trader/features/ingest/README.md`
- Modify: `src/trader/features/ingest/{feature,api}.py`
- Test: `tests/features/ingest/test_job.py`(← `test_daemon_news.py` 앞 4건 재작성 + Review Focus 2 신규 2), `test_feature.py`(신규 3), `test_public_api.py`(신규 1)

**Interfaces:**
- Consumes: Task 1 `DaemonDeps(secrets, registry, hooks, conn_factory, now, markets)`, `kernel.registry.hooks.run`, Task 8 `news_store.{collect, universe_names, prune}`, Task 11 `saveticker_ingest.ingest_news`, Task 7 `settings.load_sources`·`universe.load_universe`, `saveticker.api.{load_cfg, resolve_dir}`.
- Produces:
  - `job.py`: `news_collect(ctx, job) -> dict`: `cfg = load_sources(registry=ctx.registry)`; `not cfg.news.enabled` → `{"skipped": "news.enabled=false"}`; `universe = load_universe()`; `names_by_market = {m: news_store.universe_names(universe, {}, m) for m in ctx.markets}`(종목명 캐시 `ticker_names` 는 M7 — 빈 dict); 시장별 `news_store.collect(conn, cfg.news, ctx.secrets or Secrets(), market=m, names=…, now=ctx.now)` → `out[m]`, 예외는 `out[m] = {"error": f"{type(e).__name__}: {e}"}`; `root = resolve_dir(load_cfg(registry=ctx.registry).dir)` → `out["saveticker"] = ingest_news(conn, root, now=ctx.now, names_by_market=…)` 또는 `{"skipped": "saveticker_dir 미설정"}`(예외도 error 로); `out["pruned"] = news_store.prune(conn, before=ctx.now() - timedelta(days=cfg.news.store_retention_days))`; `out["hooks"] = hooks.run(ctx.hooks, "news.post_ingest", conn=conn, markets=ctx.markets, now=ctx.now(), result=out)`. 연결은 `ctx.conn_factory()` 하나로 열고 끝에 닫는다.
  - `JOB = JobSpec(id="news_collect", kind="news_collect", trigger="interval", per_market=False, schedule_key="news_collect.interval_s", interval_s=1800, enabled=None, gate="none", heavy=False, misfire="default", catch_up="none", start_soon_s=0, body=news_collect)` — Task 1 의 규칙으로 `schedule.yaml news_collect.interval_s`(템플릿 1800)를 읽고 키가 없으면 1800.
  - `feature.py`: `FEATURE = Feature(id="ingest", tier="service", sections=…, migrations=MIGRATIONS, jobs=(JOB,), cli=ingest_app)`.
  - `api.py`(다른 기능이 쓸 것): `SourcesConfig, NewsCfg, MacroCfg, load_sources, Universe, UsSymbol, SectorMap, load_universe, load_sectors, Section, run_section, skipped_section, owned_client, strip_tags, MAX_PAST, SKIP_REASONS, InputDeps, build_inputs, collect_and_save, jsonable, SCHEMA_VERSION, build_input_deps, build_dart, build_edgar, DartClient, DartError, ANNUAL, RATIO_KEYS, basic_ratios, EdgarClient, ratios_by_year_us, Fred, collect_macro, GoogleNewsRss, MACRO_QUERIES, news_store 함수(recent, count, novel_items, normalize_title, row_to_dict, insert_items, universe_names), disclosures.critical_recent, manual_inputs.{read, read_market, append_market}, indicators.{COLUMNS, latest, indicator_frame, to_frame}, NaverBlogSource`.

- [ ] **Step 1: job 테스트 재작성** — 옛 4건(`test_build_jobs_includes_news_collect_interval` → `jobs.build([FEATURE], schedule=<news_collect.interval_s=1800>, …)` 결과 id·interval; `test_news_body_collects_per_market_and_prunes`(respx 로 구글 RSS, 두 시장 inserted ≥ 1, 오래된 행 prune); `test_news_body_skipped_when_disabled`; `test_news_body_ingests_saveticker_folder`(fixture 트리 복사 → `out["saveticker"]["inserted"] > 0`)) + `test_news_body_isolates_market_failure`(KR 수집 예외 → `out["KR"]["error"]`, US·prune 정상) + `test_post_ingest_hook_failure_does_not_fail_job`(hooks 에 예외 훅 1개 → `out["hooks"][0]["error"]`, 반환 dict 에 `error` 키 없음). `make_deps(registry=build_registry(discover()), secrets=Secrets(), hooks=…)`. RED.
- [ ] **Step 2: job·feature·api 구현** → GREEN.
- [ ] **Step 3: feature·api 테스트** — `test_feature.py`: `test_contract_clean_and_both_features_discovered`(`[f.id for f in discover()]` ⊇ `{"accounts","ingest","saveticker"}`, `check == []`), `test_sections_cover_sources_universe_sectors`, `test_cli_mounted_as_ingest`. `test_public_api.py`: `api.__all__` 전부 import 가능. GREEN.
- [ ] **Step 4: README** — 책임·공개면·규약(옛 CLAUDE.md 「수집 계층」「룩어헤드 방지」「커뮤니티·뉴스 소스 정책」, architecture 「뉴스 저장소」「수집 보강」「공시 중요도」「유사 뉴스 판정」의 규칙을 ingest 몫만 옮긴다)·테스트·알려진 차이(아래 목록은 최소): ① `news.saveticker_*` → `saveticker` 섹션(saveticker 기능 소유) ② `disabled_sites` 삭제 ③ `edgar.user_agent` 선택(기본 `""`, 시크릿 우선) ④ `channels.naver_blog` 기본 `[]`(옛 `["ranto28"]`; 사용자 확인 TODO) ⑤ CLI `trader collect` → `trader ingest collect`, 시세 클라이언트 없이 돈다(계좌 조립은 accounts, M4 레인이 주입) ⑥ `fundamental.peer` 는 `InputDeps.peer_fn` 없으면 `no sector peers` 건너뜀(M7) ⑦ `news_collect` 는 수집·적재·prune·`news.post_ingest` 훅만 — `_news_trigger`·`_disclosure_trigger`·`_events_check`·`discover` 큐·`ticker_names` 캐시는 M7, `_vix` 는 M6, 웹 `/api/news`·수동 입력 라우트는 M11, health `disclosure` 는 M12 ⑧ `universe.yaml`·`sectors.yaml` 모델은 ingest 소유(PIT 기록·`universe check|sectors` CLI 는 M7) ⑨ `_row/_jsonable/_matches/_iso_to_utc/_kst_to_iso` 공개 이름 + 별칭 ⑩ `USER_AGENT` 브랜딩.
- [ ] **Step 5: `uv run pytest -q`·ruff·`import_violations`** — 전체 녹색.
- [ ] **Step 6: Commit** — `feat(ingest): news_collect 잡·news.post_ingest 훅·FEATURE·api·README`

---

### Task 14: 문서·체크리스트·공개면 점검

**Files:**
- Modify: `docs/architecture.md`(`## session` 신설: run_store 규칙 — 운영일·체크포인트 봉투·원자 쓰기; `## registry` 에 hooks 단락; `## daemon` 에 `DaemonDeps.secrets/registry/hooks`·teardown 한 줄; `## features 층` 에 ingest·saveticker 한 줄씩 + README 포인터), `docs/TODO.md`(M3 Task 체크 14개 + PR 행, 이연 목록: M4 `InputDeps.market` 주입·native-host argv 우회(번들)·`trader collect` 별칭 여부, M6 `_vix`, M7 `_news_trigger`·`_discover_body`·`_disclosure_trigger`·`_events_check`·`ticker_names` 캐시·`peer_fn`·`universe check|sectors`·옛 `test_daemon_news` 7건·`test_daemon_disclosure` 3건, M11 `/api/news`·`/api/control/manual-inputs`·`/api/system/saveticker`·restart, M12 health `disclosure`·`saveticker`·selfcheck playwright 점검·`test_health_disclosure_area_critical_for_held`·패키징(`extensions/saveticker` data·playwright 드라이버); 「사용자 확인 대기」에 `channels.naver_blog` 기본값·`EDGAR_USER_AGENT` 입력), `CLAUDE.md`(명령 표에 `trader ingest collect …`·`trader saveticker status|query|export|run|service|monitor|install-browser|extension …` 2줄(`run`·`service` 는 사용자 전용), 금지사항에 "`trader saveticker run|service` 는 사이트 로그인이라 사용자 요청 없이 돌리지 않는다" 1줄; ≤ 16KB 확인)
- Create: `docs/saveticker-collection.md`(← 옛 118줄, 경로·명령 이름만 갱신)

- [ ] **Step 1: 문서 작성** — 위 명세.
- [ ] **Step 2: 공개면 점검** — scratch `TRADER_HOME`(`mktemp -d`)에서 `uv run trader init` → `uv run trader selfcheck`(features 에 `accounts ingest saveticker`, `ledger_version 3`) → `uv run trader ingest collect --ticker 005930`(네트워크 없이 돌면 "오류 절 N건" exit 0; 네트워크 호출이 나가면 안 되므로 `HTTPS_PROXY=http://127.0.0.1:9` 같은 차단 env 로) → `uv run trader saveticker status`(JSON, 폴더 없음도 exit 0 인지 report) → `uv run trader saveticker extension status`. 결과를 report 에.
- [ ] **Step 3: 전체 검증** — `uv run pytest -q`, `uv run ruff check src tests`, `cd web && npm run typecheck && npm test -- --run`, `node --test extensions/saveticker/tests/*.test.js`, `uv run trader dev gen-sse-keys --check`, `wc -c CLAUDE.md` ≤ 16384, `git status` 에 `config/ secrets/ state/` 없음, `git log --format=%ae | sort -u` 가 noreply 하나.
- [ ] **Step 4: Commit** — `docs(m3): architecture session/hooks·TODO M3·CLAUDE.md 명령 표·saveticker 문서`

---

## 이식 테스트 집계(PR 본문용)

| 옛 | 건수 | 새 | 비고 |
|---|---|---|---|
| `tests/sources/*`(saveticker 리더 8 제외) | 132 | `tests/features/ingest/*` | peer 3건 M7 이연 → 129 + 신규 |
| `tests/sources/test_saveticker.py` | 8 | `saveticker/test_reader.py` 7 + `ingest/test_saveticker_ingest.py` 1 | |
| `tests/saveticker/*`(19파일) | 176 | `tests/features/saveticker/*` | 브라우저 테스트는 Chromium 없으면 skip |
| `tests/test_daemon_saveticker.py` | 6 | `saveticker/test_job.py` | JobSpec 으로 재작성 |
| `tests/test_daemon_news.py` | 11 | `ingest/test_job.py` 4 | 트리거·발굴 7건 M7 |
| `tests/test_daemon_disclosure.py` | 4 | — | M7 3·M12 1 |
| `tests/orchestrator/test_run_store.py` | 4 | `kernel/session/test_run_store.py` | |
| `tests/test_cli_collect.py` | 6 | `ingest/test_cli.py` | `ingest collect` |
| `tests/test_config.py` 유니버스 | 3 | `ingest/test_universe.py` | |
| `tests/saveticker/test_display_power.py` | 3 | `tests/test_conftest_display.py` | |
| 확장 JS `tests/*.test.js` | 26파일 | `extensions/saveticker/tests/` | `node --test`, CI Linux |
