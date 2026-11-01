# M2 — features/accounts + kernel/notify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 옛 저장소의 계좌·시크릿·모드·텔레그램 코드(`config.py` 의 accounts/secrets 부분, `control/control.py`, `accounts_io.py`, `secrets_io.py`, `notify/telegram.py`, `notify/commands.py` 골격, `notify/messages.py` 베이스, `cli.py` 의 `kis ping`·`control *`·`telegram *`·`notify-test`·`secrets import`)를 **동작 보존**으로 `kernel/config/secrets.py`·`kernel/notify/`·`features/accounts/` 에 옮기고, 옛 테스트(`tests/control/test_control.py` 모드부, `test_accounts_io.py`, `test_secrets_import.py`, `test_multi_account_keys.py`, `test_cli_control.py`, `test_cli_telegram_loop.py`, `notify/test_telegram*.py`, `notify/test_commands.py` 의 제어·오프셋·신뢰 부분, `test_config.py` 의 accounts·secrets 부분)를 새 위치로 이식해 통과시킨다. 첫 번째 **기능 폴더**이므로 레지스트리 계약(섹션·마이그레이션·CLI·텔레그램 명령·상태 절)이 실제로 돌아가는 것을 증명한다.

**Architecture:** 세 덩이. ① `kernel/config/secrets.py` — `Secrets`(필드 = 환경변수명, 계좌 전용 `KIS_<접두사>_*` 는 extra)·`load_secrets`·`kis_creds` → M1 `KisCreds` 로. 커널에 두는 이유: 커널 `notify`(텔레그램 토큰)·`web`(`WEB_TOKEN`)이 읽고, 기능이 아니라 환경이다. ② `kernel/notify/` — `telegram.py`(Notifier·outbox·Receiver·`parse_command`), `commands.py`(디스패처: 명령은 기능이 `Feature.telegram_commands` 로 등록, `/status` 는 커널 내장으로 `Feature.status_sections` 를 합친다), `messages.py` 베이스(`render`·`event`·`failure`·`money`·`pct`·`kst_hhmm`·표). ③ `features/accounts/` — `settings.py`(accounts.yaml 모델·로더·`BrokerAccount` 변환), `modes.py`(`account_modes` 테이블: 모드·LIVE 확인·실효 모드), `accounts_io.py`·`secrets_io.py`(설정 화면용 파일 편집), `brokers.py`(`_market_creds`·같은 키 경고·키 없는 계좌 skip), `cli.py`(`trader accounts show|mode|pause|resume|add|remove|ping|secrets import`), `feature.py`. 원장 v2 는 accounts 기능이 소유(`accounts`·`account_modes`). 옛 `control.mode:*`·`live_confirmed:*` 키는 테이블 열로 승격(스펙 §2.4). `paused` 는 커널 `control` 플래그 그대로.

**Tech Stack:** Python 3.12, pydantic v2(`extra="forbid"`, Secrets 만 `extra="allow"`), python-dotenv(이미 의존성), httpx + respx, sqlite3, typer. 새 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-11-02-kis-trading-agent-replatform-design.md` §2.1(`kernel/notify`·`features/accounts` 행), §2.3(Feature 필드), §2.4(`accounts`·`account_modes`·`control` 행), §2.7-1(리허설 `notify.enabled=false`), §3 M2. 옛 규칙 원문: `../trading-agent/CLAUDE.md` 「계좌 모드」「실전 `auto` 는 확인 기록이 있어야 실행한다」「계좌별 키(2026-10-27)」「전체 검토 2차 결정 반영 — 묶음 3」(신뢰할 수 없는 발신자는 조회만), 「묶음 1 즉시 비주문」(outbox), `../trading-agent/docs/architecture.md` 「계좌별 키·연결 키 입력(2026-10-27)」.

## Global Constraints

- 옛 저장소는 `../trading-agent`. 옛 `secrets/`·`config/`·`state/` 는 읽지 않는다. 이 저장소 `secrets/` 도 읽지 않는다(`trader init`·CLI 실기는 scratch `TRADER_HOME` 에서만).
- **동작 보존.** 옛 테스트의 단언을 바꾸지 않는다(import 경로·fixture·생성자·CLI 경로(`control` → `accounts`)만 바꾼다). 알려진 불일치는 고치지 않고 `features/accounts/README.md`·`docs/architecture.md` 「notify」 절의 "알려진 차이" 에 적는다.
- import 3층: `kernel` 은 `trader.features` 를 import 하지 않는다. `features/accounts` 는 `trader.kernel.*` 만 import 한다(다른 기능 없음). `registry.import_violations`·`tests/kernel/registry/test_import_boundary.py::test_real_tree_has_no_violations` 가 강제한다.
- 기능 등록은 `features/accounts/feature.py` 의 `FEATURE` 한 곳. 다른 커널 파일을 고치는 것은 **이 플랜이 명시한 커널 결함 수정**(Task 1·2·4·6)만.
- 경로는 `platform.paths` 로만(`secrets_path()`·`state_dir()`·`config_dir()`), 시각 ZoneInfo 는 `config.time.KST`. 로거 이름 `trader.config`·`trader.notify`·`trader.accounts`. 사용자 대면 메시지·docstring 한국어.
- pydantic `extra="forbid"`(예외: `Secrets` 는 `extra="allow"` — 계좌 전용 키), 금액 `Decimal`, 저장 시각 UTC ISO8601, 파일명에 `:` 금지(백업 `.bak-YYYYMMDD_HHMMSS`).
- 시크릿 값은 로그·stdout·반환값·예외 메시지 어디에도 넣지 않는다(이름과 '설정됨' 여부만). 텔레그램 토큰은 URL 에 들어가므로 URL·예외 메시지를 로그에 남기지 않는다.
- 테스트는 `project`·`conn`·`tmp_path` fixture 만 쓰고 네트워크는 respx/`httpx.MockTransport` 로 막는다. fixture 는 전부 합성(계좌번호 `00000000`, 가짜 키).
- 옛 값 그대로: `MAX_LEN = 4000`, `OUTBOX_TTL = 24h`, `OUTBOX_FLUSH_MAX = 10`, `HELP_EVERY_S = 60`, `LIVE_CONFIRM = "LIVE"`, `CANO_PLACEHOLDER = "00000000"`, `MAX_VALUE_LEN = 1024`, `_RESERVED_PREFIXES = {"KIS_PAPER_", "KIS_REAL_"}`, `_FO_PRODUCT = "03"`, `_STOCK_PRODUCT = "01"`, 텔레그램 timeout 발신 10초·수신 15초.
- 브랜치 `feat/m2-accounts-notify`, PR 대상 main, 커밋 트레일러 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, 작성자는 저장소 `git config user.email`(GitHub noreply) 그대로. ruff line-length 100. CI ubuntu+windows 녹색. `CLAUDE.md` ≤ 16KB.

## Review Focus

스펙이 암시하지만 옛 테스트가 덮지 않는 입력. 각 줄의 테스트는 해당 Task 에 있다.

1. `notify.enabled: false`(리허설 날) 이면 토큰·chat_id 가 있어도 `make_notifier` 는 `NullNotifier`, `make_receiver` 는 `None` — 두 데몬이 같은 봇을 폴링하면 명령을 서로 가로챈다. → Task 4
2. `account_modes` 행이 없을 때 `get_account_mode` 는 yaml 모드를 돌려주고, 실전 계좌를 `auto` 에서 `off` 로 내리면 `live_confirmed` 가 **지워진다**(다시 auto 로 올릴 때 새 확인이 필요). → Task 5
3. `trader accounts mode` 로 실전 `auto` 를 `--confirm LIVE` 없이 시도하면 exit 1 이고 **테이블에 아무 행도 생기지 않는다**(부분 쓰기 없음). → Task 8
4. 두 계좌가 같은 앱키 묶음으로 풀리면 현물은 WARNING, 파생은 INFO 로 **한 번** 남기고 둘 다 브로커를 만든다(거부하지 않음, 옛 동작). 키 없는 계좌는 건너뛰고 `ping` 은 그 계좌를 실패 1건으로 센다. → Task 7·8
5. 텔레그램 명령 처리 중 한 명령이 예외를 던져도 다음 명령은 처리되고 오프셋은 **업데이트마다** 전진한다(같은 명령을 영원히 다시 받지 않음). 등록되지 않은 `/xxx` 는 분당 1회 도움말. → Task 4

---

## 파일 구조

```
src/trader/kernel/
  config/__init__.py          + export: Mode, Secrets, load_secrets, kis_creds 보조(Task 1)
  config/secrets.py           신규: Secrets, CRED_SUFFIXES, CRED_PREFIX_RE, account_prefix, load_secrets   ← config.py 943~1045·1562~1579
  config/sections.py          ConfigFile 에 "accounts" 추가 (Task 2)
  config/__init__.py          KERNEL_SECTIONS 에 Section("settings","notify",NotifySettings) 추가 (Task 4)
  cli/main.py                 build_web_deps 가 WEB_TOKEN 을 load_secrets 로(Task 1), notify-test·telegram poll|loop 명령(Task 6)
  cli/boot.py                 Boot.secrets 추가(secrets=True) (Task 1)
  notify/__init__.py          공개면 re-export
  notify/messages.py          render, event, failure, money, pct, kst_hhmm, first_sentences, 표(SIDE·ACTION·MODE·ENV·REGIME·STANCE), REAL_CONFIRM   ← notify/messages.py 베이스부
  notify/telegram.py          Notifier, NullNotifier, TelegramNotifier(outbox), Update, parse_command, TelegramReceiver, make_notifier, make_receiver   ← notify/telegram.py
  notify/commands.py          신규 디스패처: CommandContext, poll_once, HELP_FLAG, HELP_EVERY_S, help_line, status_message   ← notify/commands.py 골격
  daemon/runner.py            DaemonDeps.notifier 추가 (Task 4)
src/trader/features/accounts/
  __init__.py
  README.md                   모듈 규칙(옛 architecture 「계좌별 키·연결 키 입력」 이관) + 알려진 차이
  settings.py                 Mode, Asset, Account, CANO_PLACEHOLDER, load_accounts, split_accounts, to_broker_account, SECTION   ← config.py 729~766·1401~1450
  migrations.py               MIGRATIONS = (Migration(2, "accounts", ...),)
  modes.py                    LIVE_CONFIRM, ConfirmationRequired, get_account_mode, set_account_mode, effective_mode, account_mode, sync_accounts   ← control/control.py 98~120 + executor.account_mode
  brokers.py                  market_creds, build_brokers, build_market   ← cli.py _market_creds·_build_brokers·_build_market
  accounts_io.py              AccountSpec, AccountInUse, ID_RE, add_account, remove_account, blockers   ← accounts_io.py
  secrets_io.py               NAME_MAP, import_env, CATALOG, catalog, status, update, ensure_web_token   ← secrets_io.py
  telegram.py                 TELEGRAM_COMMANDS(/pause /resume /mode), STATUS_SECTION   ← notify/commands.py _control_command 일부
  cli.py                      accounts_app: show, mode, pause, resume, add, remove, ping, secrets import
  api.py                      공개면: load_accounts, split_accounts, to_broker_account, account_mode, effective_mode, is_paused 재export(다른 기능이 쓸 것만)
  feature.py                  FEATURE
templates/config/
  accounts.yaml               신규(자리표시자 1계좌: paper-main)
  settings.yaml               + notify: {enabled: true}
tests/kernel/config/test_secrets.py, tests/kernel/notify/{test_messages,test_telegram,test_telegram_receiver,test_commands}.py,
tests/kernel/cli/test_notify_cmds.py, tests/features/__init__.py, tests/features/accounts/{conftest,test_settings,test_modes,test_brokers,test_accounts_io,test_secrets_io,test_telegram,test_cli,test_feature}.py
docs/architecture.md          ## notify 절, ## config 절에 secrets 한 줄, ## features 층 절 갱신
docs/TODO.md                  M2 체크리스트
```

옛 → 새 치환표(모든 Task 공통):

| 옛 | 새 |
|---|---|
| `from trader.config import Secrets, load_secrets, account_prefix, CRED_SUFFIXES, key_fingerprint` | `from trader.kernel.config.secrets import Secrets, load_secrets, account_prefix, CRED_SUFFIXES`; `key_fingerprint` 는 `trader.kernel.broker.creds` |
| `config.AccountConfig`, `config.Mode`, `config.Asset`, `config.CANO_PLACEHOLDER`, `config.load_accounts`, `config.split_accounts` | `trader.features.accounts.settings` 의 `Account`, `Mode`, `Asset`, `CANO_PLACEHOLDER`, `load_accounts`, `split_accounts` |
| `from trader.control import control` (모드 함수) | `from trader.features.accounts import modes` |
| `control.get_flag/set_flag/is_paused` | `trader.kernel.ledger.control.get_flag/set_flag`, `is_paused` 는 `modes.is_paused` |
| `from trader.notify.telegram import …` | `from trader.kernel.notify.telegram import …` |
| `from trader.notify import messages` | `from trader.kernel.notify import messages` |
| `commands.poll_once(ectx, receiver)` | `commands.poll_once(ctx: CommandContext, receiver)` |
| `cli._client_for/_market_creds/_build_brokers/_build_market` | `trader.kernel.broker.factory.client_for`, `trader.features.accounts.brokers.market_creds/build_brokers/build_market` |
| CLI `trader control show|mode|pause|resume` | `trader accounts show|mode|pause|resume` |
| CLI `trader kis ping` | `trader accounts ping` |
| CLI `trader secrets import` | `trader accounts secrets import` |
| `trader.ledger.db.connect/init_schema(conn)` | `trader.kernel.ledger.db.connect`, `init_schema(conn, all_migrations(features))`(테스트는 `conftest` 의 `conn`) |
| `make_account(id, env, mode)` (옛 conftest) | `tests/features/accounts/conftest.py::make_account` (`Account` 생성) |

---

### Task 1: `kernel/config/secrets.py` — `Secrets`·`load_secrets`·`kis_creds`

**Files:**
- Create: `src/trader/kernel/config/secrets.py`
- Modify: `src/trader/kernel/config/__init__.py`(export), `src/trader/kernel/cli/boot.py`(`Boot.secrets`, `boot(secrets=True)`), `src/trader/kernel/cli/main.py:58-67`(`build_web_deps` 토큰)
- Test: `tests/kernel/config/test_secrets.py`, `tests/kernel/cli/test_main.py`(`test_build_web_deps_reads_token_from_env_file` 추가)

**Interfaces:**
- Consumes: `trader.kernel.broker.creds.KisCreds`, `trader.kernel.platform.paths.secrets_path`, `trader.kernel.config.errors.ConfigError`, `trader.kernel.config.Env`.
- Produces:
  - `CRED_SUFFIXES = ("APP_KEY", "APP_SECRET", "CANO", "HTS_ID")`, `CRED_PREFIX_RE = re.compile(r"^KIS_[A-Z0-9]+(?:_[A-Z0-9]+)*_$")`
  - `account_prefix(account_id: str, credentials: str | None = None) -> str` — `credentials` 우선, 없으면 `"KIS_" + re.sub(r"[^A-Z0-9]+", "_", id.upper()).strip("_") + "_"`
  - `class Secrets(BaseModel)`(`extra="allow"`, 옛 필드 18개 그대로: `KIS_PAPER_APP_KEY … KIS_HTS_ID`) + `get(name) -> str | None`, `kis_creds(account: Any | None = None, *, env: Env | None = None) -> KisCreds` — account 는 `.id`·`.env` 와 선택적 `.credentials` 속성을 가진 아무 객체(`BrokerAccount` 또는 M2 `Account`). 옛 본문 그대로(전용 접두사에 `APP_KEY` 없으면 공용으로 폴백, `hts_id` 는 접두사 값 → `KIS_HTS_ID`).
  - `load_secrets(path: Path | None = None) -> Secrets` — `.env`(UTF-8 아니면 `ConfigError`) + 비어 있지 않은 환경변수 덮어쓰기 + `KIS_*` 접미사 환경변수 추가.
  - `Boot.secrets: Secrets | None`; `boot(secrets=True)` 가 채운다.
  - `build_web_deps` 는 `token=os.environ.get("WEB_TOKEN") or load_secrets().WEB_TOKEN` 대신 **`load_secrets().WEB_TOKEN`**(환경변수가 이미 파일을 덮는다) — M0 이연 "WEB_TOKEN 을 `secrets/.env` 에서도 읽기" 회수.

- [ ] **Step 1: 실패 테스트** — 옛 `test_config.py` 의 secrets 5건 + `test_multi_account_keys.py` 의 creds 2건 이식 + 신규 2건

```python
# tests/kernel/config/test_secrets.py
import pytest
from trader.kernel.broker.creds import key_fingerprint
from trader.kernel.broker.models import BrokerAccount
from trader.kernel.config import ConfigError
from trader.kernel.config.secrets import Secrets, account_prefix, load_secrets

def _acct(id_, env="real"):
    return BrokerAccount(id=id_, env=env, cano="00000000")

SECRETS = Secrets.model_validate({
    "KIS_REAL_APP_KEY": "shared-k", "KIS_REAL_APP_SECRET": "shared-s", "KIS_REAL_CANO": "11111111",
    "KIS_HTS_ID": "me", "KIS_REAL_2_APP_KEY": "k2", "KIS_REAL_2_APP_SECRET": "s2",
    "KIS_REAL_2_CANO": "22222222", "KIS_ISA_APP_KEY": "k3", "KIS_ISA_APP_SECRET": "s3",
})

def test_creds_prefer_account_keys_then_shared():
    main = SECRETS.kis_creds(_acct("real-main"))
    assert main.shared and main.app_key == "shared-k"
    assert main.bucket == "real-" + key_fingerprint("shared-k")
    two = SECRETS.kis_creds(_acct("real-2"))
    assert not two.shared and two.app_key == "k2" and two.cano == "22222222"
    assert two.hts_id == "me"
    same = Secrets.model_validate({"KIS_A_APP_KEY": "k2", "KIS_A_APP_SECRET": "s2"})
    assert same.kis_creds(_acct("a")).bucket == two.bucket != main.bucket
    assert SECRETS.kis_creds(env="paper").app_key is None

def test_account_prefix_rules():
    assert account_prefix("real-2") == "KIS_REAL_2_"
    assert account_prefix("isa-1", "KIS_ISA_") == "KIS_ISA_"
    assert account_prefix("--x.y--") == "KIS_X_Y_"

class _WithCreds:
    def __init__(self): self.id, self.env, self.credentials = "isa-1", "real", "KIS_ISA_"

def test_kis_creds_honours_credentials_attribute():
    isa = SECRETS.kis_creds(_WithCreds())
    assert isa.app_key == "k3" and isa.prefix == "KIS_ISA_"

def test_secrets_from_env_when_file_missing(project, monkeypatch):
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "k")
    assert load_secrets().KIS_PAPER_APP_KEY == "k"

def test_env_overrides_file(project, monkeypatch):
    (project / "secrets" / ".env").write_text("DART_API_KEY=file\nWEB_TOKEN=file\n", encoding="utf-8")
    monkeypatch.setenv("DART_API_KEY", "env"); monkeypatch.setenv("WEB_TOKEN", "")
    s = load_secrets()
    assert s.DART_API_KEY == "env" and s.WEB_TOKEN == "file"

def test_whitespace_env_does_not_override(project, monkeypatch):
    (project / "secrets" / ".env").write_text("DART_API_KEY=file\n", encoding="utf-8")
    monkeypatch.setenv("DART_API_KEY", "   ")
    assert load_secrets().DART_API_KEY == "file"

def test_cp949_env_is_config_error(project):
    (project / "secrets" / ".env").write_bytes("# 한글\nDART_API_KEY=x\n".encode("cp949"))
    with pytest.raises(ConfigError, match="UTF-8"):
        load_secrets()

def test_env_var_overrides_account_key(project, monkeypatch):
    (project / "secrets" / ".env").write_text("KIS_REAL_2_APP_KEY=file\n", encoding="utf-8")
    monkeypatch.setenv("KIS_REAL_2_APP_KEY", "env")
    assert load_secrets().get("KIS_REAL_2_APP_KEY") == "env"

def test_get_returns_none_for_blank_and_unknown():
    s = Secrets.model_validate({"DART_API_KEY": "  ", "KIS_X_APP_KEY": "v"})
    assert s.get("DART_API_KEY") is None and s.get("KIS_X_APP_KEY") == "v" and s.get("NOPE") is None
```

```python
# tests/kernel/cli/test_main.py 에 추가
def test_build_web_deps_reads_token_from_env_file(project, monkeypatch):
    monkeypatch.delenv("WEB_TOKEN", raising=False)
    (project / "secrets" / ".env").write_text("WEB_TOKEN=from-file\n", encoding="utf-8")
    b = boot_mod.boot(settings=True, conn=True)
    try:
        assert main_mod.build_web_deps(b).token == "from-file"
    finally:
        b.conn.close()

def test_boot_secrets(project):
    (project / "secrets" / ".env").write_text("DART_API_KEY=x\n", encoding="utf-8")
    b = boot_mod.boot(secrets=True)
    assert b.secrets is not None and b.secrets.DART_API_KEY == "x"
```

- [ ] **Step 2: RED** — `uv run pytest tests/kernel/config/test_secrets.py tests/kernel/cli/test_main.py -q` → ImportError.
- [ ] **Step 3: 구현** — `secrets.py` 는 옛 `config.py` 943~1045·1562~1579 를 옮기되 `KisCreds` 는 `trader.kernel.broker.creds` 에서 import, `kis_creds` 의 `account_prefix(account)` 호출을 `account_prefix(account.id, getattr(account, "credentials", None))` 로. `_validate`·`_describe` 는 `loader.py` 의 것을 재사용하지 말고(`loader` 는 섹션 레지스트리용) `Secrets.model_validate` + `ValidationError → ConfigError(f"{p.name} 검증 실패: …")` 4줄로. `boot.py` 는 `secrets: bool = False` 인자와 `Boot.secrets` 필드(기본 None). `main.build_web_deps` 는 `load_secrets().WEB_TOKEN`.
- [ ] **Step 4: GREEN** — 위 두 파일 + `uv run pytest -q` 전체 녹색(기존 `test_build_web_deps` 는 환경변수 `tok` 이 파일보다 우선이므로 그대로 통과해야 한다).
- [ ] **Step 5: Commit** — `feat(m2): kernel/config/secrets — Secrets·load_secrets·kis_creds, WEB_TOKEN 을 .env 에서도`

---

### Task 2: `features/accounts/settings.py` + `templates/config/accounts.yaml` + 기능 뼈대

**Files:**
- Create: `src/trader/features/accounts/__init__.py`, `settings.py`, `README.md`(초안 — 책임·공개면 두 줄, Task 10 에서 완성), `feature.py`(최소: `Feature(id="accounts", tier="service", sections=(SECTION,))`), `api.py`(최소)
- Modify: `src/trader/kernel/config/sections.py:13`(`ConfigFile` 에 `"accounts"`), `templates/config/accounts.yaml`(신규)
- Test: `tests/features/__init__.py`, `tests/features/accounts/__init__.py`, `tests/features/accounts/conftest.py`, `tests/features/accounts/test_settings.py`, `tests/kernel/config/test_templates.py`(parametrize 에 `"accounts"` 추가), **기존 단언 갱신 3곳**: `tests/kernel/cli/test_selfcheck.py:21`(`data["features"] == ["accounts"]`), `tests/kernel/cli/test_main.py:53`(`[f.id for f in b.features] == ["accounts"]`), `tests/kernel/registry/test_discover.py::test_discover_real_package_is_empty_list_ok` → 이름을 `test_discover_real_package_lists_accounts` 로 바꾸고 `[f.id for f in discover()] == ["accounts"]`(M0 이연 "실제 목록 단언" 회수)

**Interfaces:**
- Consumes: `kernel.config.sections.Section`, `kernel.config.loader.load`, `kernel.config.secrets.Secrets/load_secrets`, `kernel.broker.models.BrokerAccount`.
- Produces:
  - `Mode = Literal["auto", "approve", "off"]`, `Asset = Literal["stock", "fo"]`, `CANO_PLACEHOLDER = "00000000"`
  - `class Account(BaseModel)`(`extra="forbid"`): `id, env: Env, cano: str, acnt_prdt_cd: str = "01", markets: list[Market] = ["KR","US"], mode: Mode, initial_capital: Decimal | None, capital_cap: dict[Market, Decimal] | None, credentials: str | None, asset: Asset = "stock"`; 검증 `_fo_markets`(fo 는 `["KR"]` 만), `_check_prefix`(`CRED_PREFIX_RE`). **`frozen` 아님**(옛과 같이 `model_copy(update=)`).
  - `SECTION = Section("accounts", "accounts", list[Account], list)`; `_registry() -> SectionRegistry`(이 섹션 하나만)
  - `load_accounts(path: Path | None = None, *, secrets: Secrets | None = None) -> list[Account]` — `load("accounts", _registry(), path).accounts` 뒤 옛 후처리(자리표시자 cano 채움·id 중복·실전 yaml auto 거부). 파일이 없으면 `[]`.
  - `split_accounts(accounts) -> tuple[list[Account], list[Account]]`
  - `to_broker_account(a: Account) -> BrokerAccount`(id·env·cano·acnt_prdt_cd·markets·asset 복사)
  - `tests/features/accounts/conftest.py::make_account(id, env, mode, markets=None, **kw) -> Account`(cano `"00000000"`, markets 기본 `["KR"]`; 테스트는 `from tests.features.accounts.conftest import make_account` 로 가져온다 — `tests/kernel/web/test_app.py` 와 같은 방식), fixture `conn`(루트 `project` 위에서 `db.connect()` + `init_schema(conn, all_migrations(discover()))`).

- [ ] **Step 1: 실패 테스트** — 옛 `test_config.py` accounts 5건 + `test_multi_account_keys.py` 2건 + 신규

```python
# tests/features/accounts/test_settings.py
from decimal import Decimal
import pytest, yaml
from trader.kernel.config import ConfigError
from trader.kernel.config.secrets import Secrets
from trader.features.accounts import settings as S

def acc(id, env, mode, markets=("KR", "US"), **extra):
    d = {"id": id, "env": env, "cano": "00000000", "acnt_prdt_cd": "01", "markets": list(markets), "mode": mode}
    d.update(extra); return d

def write(project, data):
    (project / "config" / "accounts.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

def test_accounts_load_and_initial_capital(project):
    write(project, {"accounts": [acc("p", "paper", "auto", initial_capital="10000000")]})
    a = S.load_accounts(secrets=Secrets())
    assert a[0].initial_capital == Decimal("10000000") and a[0].acnt_prdt_cd == "01"
    assert a[0].markets == ["KR", "US"]

def test_markets_default_both(project):
    write(project, {"accounts": [{"id": "p", "env": "paper", "cano": "1", "mode": "auto"}]})
    assert S.load_accounts(secrets=Secrets())[0].markets == ["KR", "US"]

def test_accounts_unique_ids(project):
    write(project, {"accounts": [acc("a", "paper", "auto"), acc("a", "paper", "auto")]})
    with pytest.raises(ConfigError, match="중복"):
        S.load_accounts(secrets=Secrets())

def test_real_account_auto_in_yaml_rejected(project):
    write(project, {"accounts": [acc("r", "real", "auto")]})
    with pytest.raises(ConfigError, match="실전"):
        S.load_accounts(secrets=Secrets())

def test_accounts_missing_file_is_empty(project):
    assert S.load_accounts(secrets=Secrets()) == []

def test_unknown_key_is_config_error(project):
    write(project, {"accounts": [acc("p", "paper", "auto", foo=1)]})
    with pytest.raises(ConfigError, match="foo"):
        S.load_accounts(secrets=Secrets())

def test_fo_requires_kr_only(project):
    write(project, {"accounts": [acc("f", "paper", "auto", asset="fo", acnt_prdt_cd="03")]})
    with pytest.raises(ConfigError, match="KR"):
        S.load_accounts(secrets=Secrets())

def test_bad_credentials_prefix_rejected():
    with pytest.raises(ValueError):
        S.Account(id="x", env="real", cano="0", mode="off", credentials="kis_lower_")

def test_accounts_cano_placeholder_filled_from_secrets(project):
    (project / "config" / "accounts.yaml").write_text(
        'accounts:\n  - {id: p, env: paper, cano: "00000000", markets: [KR], mode: auto}\n'
        '  - {id: r, env: real, cano: "12345678", markets: [KR], mode: "off"}\n'
        '  - {id: r2, env: real, cano: "00000000", markets: [KR], mode: "off"}\n', encoding="utf-8")
    accts = {a.id: a for a in S.load_accounts(secrets=Secrets(KIS_PAPER_CANO="11112222"))}
    assert accts["p"].cano == "11112222" and accts["r"].cano == "12345678" and accts["r2"].cano == "00000000"

def test_placeholder_cano_filled_from_account_prefix(project):
    (project / "config" / "accounts.yaml").write_text(
        'accounts:\n  - {id: real-main, env: real, cano: "00000000", mode: "off"}\n'
        '  - {id: real-2, env: real, cano: "00000000", mode: "off"}\n', encoding="utf-8")
    sec = Secrets.model_validate({"KIS_REAL_APP_KEY": "k", "KIS_REAL_APP_SECRET": "s", "KIS_REAL_CANO": "11111111",
                                  "KIS_REAL_2_APP_KEY": "k2", "KIS_REAL_2_APP_SECRET": "s2", "KIS_REAL_2_CANO": "22222222"})
    assert [a.cano for a in S.load_accounts(secrets=sec)] == ["11111111", "22222222"]
    no_cano = Secrets.model_validate({**sec.model_dump(), "KIS_REAL_2_CANO": None})
    assert S.load_accounts(secrets=no_cano)[1].cano == "00000000"

def test_load_accounts_reads_secrets_file_when_not_given(project):
    (project / "config" / "accounts.yaml").write_text(
        'accounts:\n  - {id: p, env: paper, cano: "00000000", markets: [KR], mode: auto}\n', encoding="utf-8")
    (project / "secrets" / ".env").write_text("KIS_PAPER_CANO=99990000\n", encoding="utf-8")
    assert S.load_accounts()[0].cano == "99990000"

def test_split_and_to_broker_account():
    s = S.Account(id="p", env="paper", cano="1", mode="auto")
    f = S.Account(id="f", env="paper", cano="1", mode="auto", asset="fo", acnt_prdt_cd="03", markets=["KR"])
    assert S.split_accounts([s, f]) == ([s], [f])
    b = S.to_broker_account(f)
    assert (b.id, b.env, b.cano, b.acnt_prdt_cd, b.markets, b.asset) == ("f", "paper", "1", "03", ["KR"], "fo")
    assert not hasattr(b, "mode")
```

```python
# tests/kernel/config/test_templates.py — parametrize 를 ["settings", "schedule", "accounts"] 로 바꾸고 추가
def test_accounts_template_is_placeholder_only():
    text = (templates_dir() / "config" / "accounts.yaml").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert '"00000000"' in code and "env: real" not in code  # 자리표시자만, 실전 계좌 없음
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: 구현** — `settings.py` 는 옛 `AccountConfig`→`Account`(필드·검증 동일, `markets` 기본 `["KR","US"]`), `_cano_from_secrets`·`load_accounts`·`split_accounts` 본문 그대로(로더만 `load("accounts", _registry(), p)`; `load` 는 파일이 없으면 default 로 빈 목록을 만든다). `ConfigFile` Literal 에 `"accounts"` 추가. `templates/config/accounts.yaml`:

```yaml
# 계좌 목록. 계좌번호(cano)는 자리표시자로 두고 실제 번호는 secrets/.env 의 KIS_PAPER_CANO·KIS_REAL_CANO
# (계좌 전용 키를 쓰면 KIS_<ID>_CANO) 에 둔다. 실전 계좌는 mode: "off" 로 두고 CLI 로만 올린다.
accounts:
  - {id: paper-main, env: paper, cano: "00000000", acnt_prdt_cd: "01", markets: [KR, US], mode: auto}
```

`feature.py` 는 `FEATURE = Feature(id="accounts", tier="service", sections=(SECTION,))`(이후 Task 가 필드를 더한다). `README.md` 는 제목 + 「책임」 한 단락(계약 검사 통과용, Task 10 완성).

- [ ] **Step 4: GREEN** — `uv run pytest -q` 전체(위 기존 단언 3곳 갱신 포함). `test_real_tree_has_no_violations` 가 `accounts` 를 받아들이는지 확인(README 존재·import 경계).
- [ ] **Step 5: Commit** — `feat(m2): features/accounts settings — accounts.yaml 모델·로더·템플릿`

---

### Task 3: `kernel/notify/messages.py` + `kernel/notify/telegram.py`

**Files:**
- Create: `src/trader/kernel/notify/__init__.py`, `messages.py`, `telegram.py`
- Test: `tests/kernel/notify/__init__.py`, `test_messages.py`, `test_telegram.py`, `test_telegram_receiver.py`

**Interfaces:**
- Consumes: `kernel.config.secrets.Secrets`, `kernel.config.time.KST`, `kernel.platform.logging_setup.quiet_httpx`, `kernel.platform.filelock.locked`, `kernel.platform.paths.state_dir`.
- Produces(`messages.py`, 옛 베이스부만): `SIDE, ACTION, MODE, ENV, REGIME, STANCE, DRY_RUN, REAL_CONFIRM = "실전"`, `money(v, market) -> str`, `pct(v, digits=0) -> str`, `first_sentences(text: str, n: int = 2) -> str`, `kst_hhmm(iso: str | None) -> str`, `render(tag, title="", lines=(), *, action: str | None = None) -> str`(옛 시그니처에서 `opinion`·`market` 인자는 M4 가 더한다 — 지금은 없음), `event(tag, text="", *lines) -> str`, `failure(tag, scope, err) -> str`, `mode_changed(*, account, mode) -> str`.
- Produces(`telegram.py`, 옛 모듈 전체): `MAX_LEN`, `OUTBOX_TTL`, `OUTBOX_FLUSH_MAX`, `Notifier`(Protocol), `NullNotifier`, `TelegramNotifier(token, chat_id, *, http=None, outbox: Path | None = None)` + `send(text, *, escape=True) -> bool`, `flush_outbox(*, now=None) -> dict`, `Update(update_id, text, chat_id, sender_id="", trusted=True)`, `parse_command(text) -> tuple[str, str | None] | None`, `TelegramReceiver(token, chat_id, *, http=None, allowed_users=None)` + `poll(offset=None) -> list[Update]`·`last_update_id`, `make_notifier(secrets, *, http=None, enabled: bool = True) -> Notifier`, `make_receiver(secrets, *, http=None, enabled: bool = True) -> TelegramReceiver | None`.
- **`parse_command` 의 명령 집합**: 옛 정규식은 명령 이름을 열거했다(`approve|reject|…`). 새 디스패처는 기능이 명령을 등록하므로 `parse_command` 는 **이름 형식만** 본다: `r"^/([a-z_]+)(?:@\w+)?(?:\s+(.+?))?\s*$"`. 모르는 이름의 거부(도움말)는 `commands.poll_once` 몫. 옛 테스트 `parse_command("/foo") is None` 단언 하나만 `== ("foo", None)` 로 바꾸고 README 「알려진 차이」에 적는다(이식 플랜이 허용한 "단언 변경" 유일 건, PR 본문에 나열).

- [ ] **Step 1: 실패 테스트** — 옛 `notify/test_telegram.py` 12건·`test_telegram_receiver.py` 6건 이식(치환표 적용, `make_notifier(Secrets())` 그대로) + `test_messages.py` 베이스 4건 + 신규

```python
# tests/kernel/notify/test_messages.py
from decimal import Decimal
from trader.kernel.notify import messages as M

def test_money_and_sentences():
    assert M.money(Decimal("71500"), "KR") == "71,500원"
    assert M.money("227.5", "US") == "$227.50"
    assert M.money(None, "KR") == "—"
    assert M.first_sentences("가. 나. 다.") == "가. 나."

def test_pct_and_kst():
    assert M.pct(0.1234, 1) == "12.3%" and M.pct("x") == "—"
    assert M.kst_hhmm("2026-10-26T00:30:00+00:00") == "09:30" and M.kst_hhmm(None) == "—"

def test_render_event_failure_mode():
    assert M.event("상태", "실행 중", "", "둘째") == "[상태] 실행 중\n둘째"
    assert M.failure("명령 실패", "/x", RuntimeError("boom")) == "[명령 실패] /x\n오류: RuntimeError: boom"
    assert M.mode_changed(account="p", mode="approve") == "[모드] p → 승인"
    assert M.render("태그", "", (), action="/approve 1") == "[태그]\n\n/approve 1"
```

```python
# tests/kernel/notify/test_telegram.py 에 추가 (Review Focus 1)
def test_disabled_flag_wins_over_secrets():
    s = Secrets(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c")
    assert isinstance(make_notifier(s, enabled=False), NullNotifier)
    assert make_receiver(s, enabled=False) is None

def test_make_notifier_outbox_lives_in_state_dir(project):
    n = make_notifier(Secrets(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c"))
    assert n._outbox == project.resolve() / "state" / "notify_outbox.jsonl"
```

```python
# tests/kernel/notify/test_telegram_receiver.py — 옛 단언 중 한 줄만 변경
    assert parse_command("안녕") is None and parse_command("/foo") == ("foo", None)
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: 구현** — `telegram.py` 는 옛 파일 본문 그대로, import 치환, `_COMMAND_RE` 를 위 형식 정규식으로, `make_notifier/make_receiver` 에 `enabled` 키워드(False 면 `NullNotifier`/`None`). `messages.py` 는 옛 1~60·132~153·199~203·268~284·514~516 줄의 베이스 함수와 표만(주문·승인·결정 문구는 M4·M5 가 더한다), `first_sentences` 는 옛 구현 그대로, `kst_hhmm` 은 `config.time.KST`.
- [ ] **Step 4: GREEN** — `uv run pytest tests/kernel/notify -q`.
- [ ] **Step 5: Commit** — `feat(m2): kernel/notify telegram·messages — 발신·outbox·수신·문구 베이스`

---

### Task 4: `kernel/notify/commands.py` 디스패처 + `notify` 설정 섹션 + `DaemonDeps.notifier`

**Files:**
- Create: `src/trader/kernel/notify/commands.py`
- Modify: `src/trader/kernel/config/__init__.py`(`NotifySettings`, `KERNEL_SECTIONS` 에 `Section("settings","notify",NotifySettings,NotifySettings)`), `templates/config/settings.yaml`(`notify:\n  enabled: true`), `src/trader/kernel/daemon/runner.py:35-46`(`DaemonDeps.notifier: Any`), `src/trader/kernel/cli/main.py:44-55`(`build_daemon_deps` 가 `make_notifier(load_secrets(), enabled=settings.notify.enabled)`), `src/trader/kernel/registry/types.py:91-96`(`TelegramCommand.handler: Callable[[Any, str | None], str]` 주석, `StatusSection.render: Callable[[Any], list[str]]` 주석 — 타입 힌트만, 필드 변경 없음)
- Test: `tests/kernel/notify/test_commands.py`, `tests/kernel/config/test_templates.py`(`test_kernel_defaults` 에 `cfg.notify.enabled is True`), `tests/kernel/cli/test_main.py`(`test_build_daemon_deps` 에 `isinstance(deps.notifier, NullNotifier)` — 시크릿 없음)

**Interfaces:**
- Consumes: `kernel.ledger.control.get_flag/set_flag`, `kernel.notify.telegram.parse_command`, `kernel.notify.messages`, `kernel.registry.types.Feature/TelegramCommand/StatusSection`.
- Produces:
  - `class NotifySettings(BaseModel)`(`extra="forbid"`): `enabled: bool = True`, `poll_interval_s: int = Field(30, ge=5, le=600)`.
  - `@dataclass CommandContext`: `conn: sqlite3.Connection`, `settings: Any`, `state: Path`, `now: Callable[[], datetime]`, `notifier: Notifier`, `features: list[Feature]`, `echo: Callable[[str], None] = lambda s: None`.
  - 명령 핸들러 계약: `TelegramCommand.handler(ctx: CommandContext, arg: str | None) -> str`(돌려준 문자열을 `ctx.notifier.send` 로 보낸다). `StatusSection.render(ctx: CommandContext) -> list[str]`.
  - `OFFSET_FLAG = "telegram_offset"`, `HELP_FLAG = "telegram_help_at"`, `HELP_EVERY_S = 60`, `STATUS = "status"`.
  - `registry(features) -> dict[str, TelegramCommand]`(이름 중복이면 `RegistryError`), `help_line(features) -> str`(`/status` + 등록 명령의 `help_line` 을 ` · ` 로), `status_message(ctx) -> str`(`messages.event("상태", *lines)` — `features` 순서대로 각 `status_sections` 의 `render(ctx)` 줄; 절이 없으면 `"등록된 상태 절 없음"`), `poll_once(ctx, receiver) -> dict{received, handled, failed}` — 옛 본문 그대로의 흐름: `offset = get_flag(OFFSET_FLAG)` → `receiver.poll(offset)` → 업데이트마다 `parse_command`; 없는 이름(`/` 로 시작) 은 `_help_due` 면 `event("명령 안내", help_line)`; `trusted=False` 이고 `(name == "status" 이거나 cmd.untrusted_ok)` 가 아니면 `event("명령 거부", …)`; 아니면 handler 실행, 예외는 `failure("명령 실패", text, e)` + `failed += 1`; 업데이트마다 `set_flag(OFFSET_FLAG, update_id + 1)`; 끝에 `receiver.last_update_id` 포함 최대 id + 1; 마지막에 `ctx.echo(f"수신 {n}건, 명령 {handled}건" + (f", 실패 {failed}건" if failed else ""))`. `executor.expire_approvals` 호출은 M4 가 훅으로 더한다(지금 없음 — README 알려진 차이).
  - `DaemonDeps.notifier: Any = None`(기본값을 두어 `tests/kernel/daemon/test_runner.py` 의 `DaemonDeps(**base)` 가 그대로 돈다); `build_daemon_deps` 는 `make_notifier(boot_.secrets or load_secrets(), enabled=boot_.settings.notify.enabled)` 를 넣는다.

- [ ] **Step 1: 실패 테스트** — 옛 `test_commands.py` 의 `test_parse_multiword_commands`·`test_poll_once_advances_offset_past_filtered_updates`·`test_command_failure_does_not_block_others_or_offset`·`test_unknown_command_help_at_most_once_per_minute`·`test_untrusted_sender_can_only_read` 를 **가짜 기능**으로 이식 + 신규

```python
# tests/kernel/notify/test_commands.py
from datetime import UTC, datetime, timedelta
import pytest
from trader.kernel.ledger import control
from trader.kernel.notify import commands
from trader.kernel.notify.telegram import Update, parse_command
from trader.kernel.registry.types import Feature, RegistryError, StatusSection, TelegramCommand

class Sent:
    def __init__(self): self.msgs = []
    def send(self, text, *, escape=True): self.msgs.append(text); return True

class Recv:
    last_update_id = None
    def __init__(self, texts, trusted=True): self.texts, self.trusted = texts, trusted
    def poll(self, offset=None):
        return [Update(update_id=i + 1, text=t, chat_id="1", trusted=self.trusted) for i, t in enumerate(self.texts)]

def _feat(store):
    def pause(ctx, arg): store["paused"] = True; return "[일시정지] 멈췄어요"
    def boom(ctx, arg): raise RuntimeError("database is locked")
    return Feature(id="demo", tier="service",
        telegram_commands=(TelegramCommand("pause", False, "/pause", pause),
                           TelegramCommand("approve", False, "/approve <id>", boom),
                           TelegramCommand("daily", True, "/daily", lambda c, a: "[일일] ok")),
        status_sections=(StatusSection("계좌", lambda ctx: ["실행 중 · 계좌 모드: p 자동"]),))

def _ctx(conn, sent, store, now=None):
    t = now or (lambda: datetime(2026, 10, 28, 1, 0, tzinfo=UTC))
    return commands.CommandContext(conn=conn, settings=None, state=None, now=t, notifier=sent, features=[_feat(store)])

def test_parse_multiword_commands():
    assert parse_command("/mode paper-main approve") == ("mode", "paper-main approve")
    assert parse_command("/watch add 000660") == ("watch", "add 000660")
    assert parse_command("/pause") == ("pause", None) and parse_command("/resume@bot") == ("resume", None)
    assert parse_command("hello") is None

def test_dispatch_and_builtin_status(conn):
    sent, store = Sent(), {}
    r = commands.poll_once(_ctx(conn, sent, store), Recv(["/pause", "/status"]))
    assert r == {"received": 2, "handled": 2, "failed": 0} and store["paused"]
    assert sent.msgs[0] == "[일시정지] 멈췄어요" and sent.msgs[1].startswith("[상태]") and "계좌 모드" in sent.msgs[1]

def test_poll_once_advances_offset_past_filtered_updates(conn):
    class FilteredRecv(Recv): last_update_id = 50
    sent, store = Sent(), {}
    r = commands.poll_once(_ctx(conn, sent, store), FilteredRecv([]))
    assert r["received"] == 0 and control.get_flag(conn, "telegram_offset") == 51
    class MixedRecv(Recv): last_update_id = 1
    commands.poll_once(_ctx(conn, sent, store), MixedRecv(["/status", "/status"]))
    assert control.get_flag(conn, "telegram_offset") == 3

def test_command_failure_does_not_block_others_or_offset(conn):
    sent, store = Sent(), {}
    r = commands.poll_once(_ctx(conn, sent, store), Recv(["/approve 7", "/pause"]))
    assert r["handled"] == 2 and r["failed"] == 1 and store["paused"]
    assert control.get_flag(conn, "telegram_offset") == 3
    assert any("[명령 실패]" in m and "/approve 7" in m for m in sent.msgs)

def test_unknown_command_help_at_most_once_per_minute(conn):
    sent, store = Sent(), {}
    t = [datetime(2026, 10, 28, 1, 0, tzinfo=UTC)]
    ctx = _ctx(conn, sent, store, now=lambda: t[0])
    commands.poll_once(ctx, Recv(["/aprove 1", "/foo"]))
    assert sum(m.startswith("[명령 안내]") for m in sent.msgs) == 1 and "/status" in sent.msgs[-1] and "/pause" in sent.msgs[-1]
    t[0] += timedelta(seconds=61)
    commands.poll_once(ctx, Recv(["/bar"]))
    assert sum(m.startswith("[명령 안내]") for m in sent.msgs) == 2
    assert control.get_flag(conn, "telegram_offset") == 2

def test_untrusted_sender_can_only_read(conn):
    sent, store = Sent(), {}
    ctx = _ctx(conn, sent, store)
    commands.poll_once(ctx, Recv(["/pause"], trusted=False))
    assert sent.msgs[-1].startswith("[명령 거부]") and "paused" not in store
    commands.poll_once(ctx, Recv(["/status", "/daily"], trusted=False))
    assert not any(m.startswith("[명령 거부]") for m in sent.msgs[-2:])

def test_duplicate_command_names_rejected():
    f = Feature(id="a", tier="service", telegram_commands=(TelegramCommand("x", True, "", lambda c, a: ""),))
    g = Feature(id="b", tier="service", telegram_commands=(TelegramCommand("x", True, "", lambda c, a: ""),))
    with pytest.raises(RegistryError, match="x"):
        commands.registry([f, g])
    with pytest.raises(RegistryError, match="status"):
        commands.registry([Feature(id="c", tier="service", telegram_commands=(TelegramCommand("status", True, "", lambda c, a: ""),))])

def test_status_without_sections(conn):
    sent = Sent()
    ctx = commands.CommandContext(conn=conn, settings=None, state=None, now=lambda: datetime.now(UTC), notifier=sent, features=[])
    commands.poll_once(ctx, Recv(["/status"]))
    assert "등록된 상태 절 없음" in sent.msgs[-1]
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: 구현** — `commands.py` 위 Interfaces 대로. `registry` 는 `"status"` 예약(기능이 등록하면 `RegistryError`). `_help_due` 는 옛 본문 그대로(`ctx.now()` 와 `HELP_FLAG`). `NotifySettings`·섹션·템플릿·`DaemonDeps.notifier`(끝에 기본값 `None` 으로 추가; `build_daemon_deps` 가 채움)·기존 `test_build_daemon_deps` 보강.
- [ ] **Step 4: GREEN** — `uv run pytest tests/kernel -q`(`test_templates`·`test_main`·`daemon` 전부).
- [ ] **Step 5: Commit** — `feat(m2): kernel/notify commands — 명령 디스패처·내장 /status·notify 설정 섹션`

---

### Task 5: `features/accounts/migrations.py` + `modes.py`

**Files:**
- Create: `src/trader/features/accounts/migrations.py`, `modes.py`
- Modify: `feature.py`(`migrations=MIGRATIONS`)
- Test: `tests/features/accounts/test_modes.py`

**Interfaces:**
- Consumes: `kernel.ledger.migrations.Migration`, `kernel.ledger.db.now_iso`, `kernel.ledger.control.get_flag/set_flag`, `settings.Account/Mode`.
- Produces:
  - `MIGRATIONS = (Migration(2, "accounts", (...), owner="accounts"),)` — 테이블 두 개:
    ```sql
    CREATE TABLE accounts (
      id TEXT PRIMARY KEY, env TEXT NOT NULL CHECK(env IN ('paper','real')),
      asset TEXT NOT NULL CHECK(asset IN ('stock','fo')), acnt_prdt_cd TEXT NOT NULL,
      markets_json TEXT NOT NULL, yaml_mode TEXT NOT NULL CHECK(yaml_mode IN ('auto','approve','off')),
      first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL)
    CREATE TABLE account_modes (
      account_id TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK(mode IN ('auto','approve','off')),
      live_confirmed_json TEXT, updated_at TEXT NOT NULL)
    ```
  - `LIVE_CONFIRM = "LIVE"`, `class ConfirmationRequired(Exception)`, `REAL_FO_OFF_NOTE = "실전 파생 계좌는 주문 없음(파생 레인 범위 밖)"`, `LIVE_UNCONFIRMED_NOTE = "실전 auto 는 확인 기록(live_confirmed) 없이는 off 로 취급"`
  - `sync_accounts(conn, accounts: list[Account]) -> None`(upsert; `first_seen_at` 은 최초만)
  - `get_account_mode(conn, account) -> Mode`(행 있으면 그 값, 없으면 `account.mode`)
  - `set_account_mode(conn, account, mode: str, *, confirm: str | None = None) -> None`(옛 검증 그대로: 모드 집합 밖 `ValueError`, 실전 auto 는 `confirm == LIVE_CONFIRM` 아니면 `ConfirmationRequired`; 실전이면 `live_confirmed_json` 을 auto 일 때 `{"confirm", "at"}`, 아니면 NULL; **같은 값이면 쓰지 않아 `updated_at` 불변**)
  - `live_confirmed(conn, account_id) -> dict | None`
  - `effective_mode(conn, account) -> tuple[Mode, str | None]`(실전 auto + 확인 없음 → `("off", LIVE_UNCONFIRMED_NOTE)`)
  - `account_mode(conn, account) -> tuple[Mode, str | None]`(실전 fo → `("off", REAL_FO_OFF_NOTE)`, 그 외 `effective_mode`)
  - `mode_source(conn, account_id) -> Literal["control", "yaml"]`
  - `is_paused(conn) -> bool`, `set_paused(conn, value: bool) -> None`(커널 `control` 의 `paused` 플래그)

- [ ] **Step 1: 실패 테스트** — 옛 `test_control.py` 모드 3건 + `test_commands.py::test_account_mode_real_fo_is_always_off` + 신규(Review Focus 2)

```python
# tests/features/accounts/test_modes.py
import pytest
from tests.features.accounts.conftest import make_account
from trader.features.accounts import modes
from trader.features.accounts.settings import Account

def _fo(id, env, mode):
    return Account(id=id, env=env, cano="00000000", acnt_prdt_cd="03", markets=["KR"], mode=mode, asset="fo")

def test_schema_has_accounts_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"accounts", "account_modes"} <= names

def test_mode_default_from_yaml_then_control(conn):
    acc = make_account("paper-main", "paper", "auto")
    assert modes.get_account_mode(conn, acc) == "auto" and modes.mode_source(conn, acc.id) == "yaml"
    modes.set_account_mode(conn, acc, "off")
    assert modes.get_account_mode(conn, acc) == "off" and modes.mode_source(conn, acc.id) == "control"

def test_real_auto_requires_confirm(conn):
    acc = make_account("real-main", "real", "off")
    with pytest.raises(modes.ConfirmationRequired):
        modes.set_account_mode(conn, acc, "auto")
    assert conn.execute("SELECT COUNT(*) FROM account_modes").fetchone()[0] == 0
    modes.set_account_mode(conn, acc, "auto", confirm="LIVE")
    assert modes.get_account_mode(conn, acc) == "auto"
    assert modes.live_confirmed(conn, "real-main")["confirm"] == "LIVE"
    modes.set_account_mode(conn, acc, "off")
    assert modes.live_confirmed(conn, "real-main") is None  # 내리면 확인 기록 삭제

def test_unknown_mode_rejected(conn):
    with pytest.raises(ValueError):
        modes.set_account_mode(conn, make_account("p", "paper", "auto"), "yolo")

def test_same_value_does_not_touch_updated_at(conn):
    acc = make_account("p", "paper", "auto")
    modes.set_account_mode(conn, acc, "approve")
    t1 = conn.execute("SELECT updated_at FROM account_modes WHERE account_id='p'").fetchone()[0]
    modes.set_account_mode(conn, acc, "approve")
    assert conn.execute("SELECT updated_at FROM account_modes WHERE account_id='p'").fetchone()[0] == t1

def test_effective_and_account_mode(conn):
    real = make_account("real-main", "real", "approve")
    assert modes.account_mode(conn, real) == ("approve", None)
    conn.execute("INSERT INTO account_modes VALUES ('real-main','auto',NULL,'t')"); conn.commit()
    assert modes.effective_mode(conn, real) == ("off", modes.LIVE_UNCONFIRMED_NOTE)
    assert modes.account_mode(conn, _fo("real-fo", "real", "approve"))[0] == "off"
    assert modes.account_mode(conn, _fo("paper-fo", "paper", "auto"))[0] == "auto"

def test_sync_accounts_upserts(conn):
    a = make_account("p", "paper", "auto", markets=["KR", "US"])
    modes.sync_accounts(conn, [a]); modes.sync_accounts(conn, [a.model_copy(update={"mode": "approve"})])
    row = conn.execute("SELECT env, asset, markets_json, yaml_mode FROM accounts WHERE id='p'").fetchone()
    assert tuple(row) == ("paper", "stock", '["KR", "US"]', "approve")
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1

def test_paused_flag(conn):
    assert modes.is_paused(conn) is False
    modes.set_paused(conn, True); assert modes.is_paused(conn) is True
    modes.set_paused(conn, False); assert modes.is_paused(conn) is False
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: 구현** — `set_account_mode` 는 `INSERT … ON CONFLICT(account_id) DO UPDATE … WHERE account_modes.mode IS NOT excluded.mode OR account_modes.live_confirmed_json IS NOT excluded.live_confirmed_json` 한 문장 + commit(검증은 쓰기 전에 전부). `live_confirmed_json` 은 `json.dumps({"confirm": confirm, "at": now_iso()})`.
- [ ] **Step 4: GREEN** — `uv run pytest tests/features/accounts tests/kernel/registry tests/kernel/ledger -q`(마이그레이션 연속성 v1→v2 통과).
- [ ] **Step 5: Commit** — `feat(m2): accounts 원장 v2 — accounts·account_modes 테이블, 모드·LIVE 확인·실효 모드`

---

### Task 6: 커널 CLI `notify-test`·`telegram poll|loop`

**Files:**
- Modify: `src/trader/kernel/cli/main.py`(명령 2종 + `telegram_app` sub-typer; `build_command_ctx(boot_, *, echo) -> CommandContext`)
- Test: `tests/kernel/cli/test_notify_cmds.py`

**Interfaces:**
- Consumes: Task 1 `boot(secrets=True)`, Task 3 `make_notifier/make_receiver`, Task 4 `CommandContext/poll_once`, `kernel.config.time.KST`.
- Produces: `trader notify-test`(`make_notifier(secrets, enabled=settings.notify.enabled).send(f"trader 알림 테스트 {stamp}")` → "전송 성공"/"전송 실패"+exit 1), `trader telegram poll`, `trader telegram loop [--interval N] [--max-iterations N(hidden)]`(`interval` 기본은 `settings.notify.poll_interval_s`; 수신기 없으면 "오류: 텔레그램 토큰·chat_id 가 없습니다" exit 1; 루프마다 `poll_once` + `notifier.flush_outbox()`(TelegramNotifier 일 때)). 로그 파일 이름 `telegram`.

- [ ] **Step 1: 실패 테스트** — 옛 `test_cli_telegram_loop.py` 이식 + notify-test

```python
# tests/kernel/cli/test_notify_cmds.py
from typer.testing import CliRunner
from trader.kernel.cli import main as main_mod
from trader.kernel.notify.telegram import Update

class FakeReceiver:
    last_update_id = None
    def __init__(self): self.n = 0
    def poll(self, offset=None):
        self.n += 1
        return [Update(update_id=self.n, text="/status", chat_id="1")]

class Sent:
    def __init__(self): self.msgs = []
    def send(self, text, *, escape=True): self.msgs.append(text); return True

def test_cli_telegram_loop_runs_n_times(project, monkeypatch):
    recv, sent = FakeReceiver(), Sent()
    monkeypatch.setattr(main_mod, "make_receiver", lambda s, **kw: recv)
    monkeypatch.setattr(main_mod, "make_notifier", lambda s, **kw: sent)
    r = CliRunner().invoke(main_mod.app, ["telegram", "loop", "--interval", "0", "--max-iterations", "2"])
    assert r.exit_code == 0, r.stdout
    assert recv.n == 2 and len(sent.msgs) == 2 and r.stdout.count("명령 1건") == 2

def test_cli_telegram_poll_without_secrets_exits_1(project):
    r = CliRunner().invoke(main_mod.app, ["telegram", "poll"])
    assert r.exit_code == 1 and "토큰" in (r.stdout + (r.stderr or ""))

def test_cli_notify_test_null_notifier_succeeds(project):
    r = CliRunner().invoke(main_mod.app, ["notify-test"])
    assert r.exit_code == 0 and "전송 성공" in r.stdout

def test_cli_notify_test_failure_exits_1(project, monkeypatch):
    class Bad:
        def send(self, text, *, escape=True): return False
    monkeypatch.setattr(main_mod, "make_notifier", lambda s, **kw: Bad())
    assert CliRunner().invoke(main_mod.app, ["notify-test"]).exit_code == 1
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: 구현** — `main.py` 에 `from trader.kernel.notify.telegram import make_notifier, make_receiver`(모듈 속성으로 두어 monkeypatch 가능), `build_command_ctx(b, *, echo)` 는 `CommandContext(conn=b.conn, settings=b.settings, state=paths.state_dir(), now=lambda: datetime.now(UTC), notifier=make_notifier(b.secrets, enabled=b.settings.notify.enabled), features=b.features, echo=echo)`. `telegram loop` 는 `boot(settings=True, secrets=True, conn=True, log_name="telegram")`, `time.sleep(interval)`. `test_help_lists_core_commands` 가 있으면 `notify-test`·`telegram` 을 기대 목록에 더한다.
- [ ] **Step 4: GREEN** — `uv run pytest tests/kernel/cli -q`.
- [ ] **Step 5: Commit** — `feat(m2): CLI notify-test·telegram poll|loop`

---

### Task 7: `features/accounts/brokers.py` — 시세 키 선택·같은 키 경고·브로커 조립

**Files:**
- Create: `src/trader/features/accounts/brokers.py`
- Test: `tests/features/accounts/test_brokers.py`

**Interfaces:**
- Consumes: `kernel.broker.factory.client_for/build_market/build_broker/reset_clients`, `kernel.broker.models.FoProduct`, `kernel.config.secrets.Secrets/account_prefix`, `settings.Account/to_broker_account`.
- Produces:
  - `market_creds(secrets: Secrets, accounts: list[Account] | None = None) -> KisCreds` — 옛 `_market_creds`(공용 `KIS_REAL_*` → 첫 실전 **현물** 계좌 전용 키 → 공용). `accounts=None` 이면 `load_accounts(secrets=secrets)`(ConfigError 면 공용).
  - `build_market(secrets, accounts=None) -> KisMarket`
  - `build_brokers(secrets, accounts: list[Account], *, fo_product: FoProduct | None = None) -> list[KisBroker | KisFoBroker]` — 옛 `_build_brokers`: `(env, prefix)` 소유자 표로 같은 키 경고(현물 WARNING·파생 INFO, 로거 `trader.accounts`, 메시지 옛 문구 그대로 `"계좌 %s 가 %s 와 같은 앱키(%s*)를 씁니다 — 계좌 전용 키(%s*)를 넣으세요"`), 키 없음(`client_for` 의 `ValueError`) 은 `warning("계좌 %s 건너뜀: %s 앱키·시크릿(%sAPP_KEY/SECRET)이 secrets/.env 에 없습니다")` 후 continue, fo 계좌는 `fo_product` 가 없으면 `warning("계좌 %s 건너뜀: 파생 상품 설정(settings.fo)이 없습니다 — M10 에서 연결")` 후 continue(M10 이 `settings.fo.product_cfg()` 를 넘긴다).

- [ ] **Step 1: 실패 테스트** — 옛 `test_multi_account_keys.py` 2건 이식 + Review Focus 4

```python
# tests/features/accounts/test_brokers.py
import logging
import pytest
from trader.kernel.broker import factory
from trader.kernel.broker.creds import key_fingerprint
from trader.kernel.config.secrets import Secrets
from trader.features.accounts import brokers
from trader.features.accounts.settings import Account

SECRETS = Secrets.model_validate({
    "KIS_REAL_APP_KEY": "shared-k", "KIS_REAL_APP_SECRET": "shared-s", "KIS_REAL_CANO": "11111111",
    "KIS_HTS_ID": "me", "KIS_REAL_2_APP_KEY": "k2", "KIS_REAL_2_APP_SECRET": "s2", "KIS_REAL_2_CANO": "22222222",
})

def _acct(id_, env="real", **kw):
    return Account(id=id_, env=env, cano="00000000", mode="off", **kw)

@pytest.fixture(autouse=True)
def _reset(project):
    factory.reset_clients(); yield; factory.reset_clients()

def test_brokers_use_per_key_clients_and_warn_on_shared_key(caplog):
    accts = [_acct("real-main"), _acct("real-2"), _acct("real-3")]
    with caplog.at_level(logging.WARNING, logger="trader.accounts"):
        built = {b.account.id: b for b in brokers.build_brokers(SECRETS, accts)}
    assert built["real-2"].client is not built["real-main"].client
    fp = key_fingerprint("k2")
    assert built["real-2"].client.auth.cache_path.name == f"kis_token_real-{fp}.json"
    assert built["real-3"].client is built["real-main"].client
    assert sum("real-3" in r.message and "KIS_REAL_3_" in r.message for r in caplog.records) == 1

def test_fo_shared_key_is_info_not_warning(caplog):
    fo = _acct("paper-fo", env="paper", asset="fo", acnt_prdt_cd="03", markets=["KR"])
    stock = _acct("paper-main", env="paper")
    sec = Secrets(KIS_PAPER_APP_KEY="pk", KIS_PAPER_APP_SECRET="ps")
    with caplog.at_level(logging.INFO, logger="trader.accounts"):
        out = brokers.build_brokers(sec, [stock, fo])
    assert [b.account.id for b in out] == ["paper-main"]  # fo_product 없음 → 건너뜀(경고)
    assert any(r.levelno == logging.INFO and "paper-fo" in r.message and "같은 앱키" in r.message for r in caplog.records)
    assert any(r.levelno == logging.WARNING and "paper-fo" in r.message and "건너뜀" in r.message for r in caplog.records)

def test_missing_key_account_is_skipped_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="trader.accounts"):
        out = brokers.build_brokers(Secrets(), [_acct("paper-main", env="paper")])
    assert out == [] and any("건너뜀" in r.message and "KIS_PAPER_APP_KEY" in r.message for r in caplog.records)
    assert not any("secret" in r.message.lower() and "ps" in r.message for r in caplog.records)

def test_market_falls_back_to_first_real_stock_account_key():
    only = Secrets.model_validate({"KIS_REAL_2_APP_KEY": "k2", "KIS_REAL_2_APP_SECRET": "s2"})
    assert brokers.market_creds(only, [_acct("real-2")]).app_key == "k2"
    assert brokers.market_creds(SECRETS, [_acct("real-2")]).app_key == "shared-k"
    fo_only = Secrets.model_validate({"KIS_REAL_FO_APP_KEY": "kf", "KIS_REAL_FO_APP_SECRET": "sf"})
    assert brokers.market_creds(fo_only, [_acct("real-fo", asset="fo", acnt_prdt_cd="03", markets=["KR"])]).app_key is None

def test_market_creds_loads_accounts_when_not_given(project):
    (project / "config" / "accounts.yaml").write_text(
        'accounts:\n  - {id: real-2, env: real, cano: "00000000", markets: [KR], mode: "off"}\n', encoding="utf-8")
    only = Secrets.model_validate({"KIS_REAL_2_APP_KEY": "k2", "KIS_REAL_2_APP_SECRET": "s2"})
    assert brokers.market_creds(only).app_key == "k2"
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: 구현** — 위 Interfaces. 브로커 생성은 `factory.build_broker(to_broker_account(a), creds, fo_product=fo_product)`.
- [ ] **Step 4: GREEN.**
- [ ] **Step 5: Commit** — `feat(m2): accounts brokers — 시세 키 선택·같은 키 경고·키 없는 계좌 skip`

---

### Task 8: `features/accounts/cli.py` — `show|mode|pause|resume|ping` + `feature.py` 등록

**Files:**
- Create: `src/trader/features/accounts/cli.py`
- Modify: `feature.py`(`cli=accounts_app`)
- Test: `tests/features/accounts/test_cli.py`

**Interfaces:**
- Consumes: Task 1 `boot(secrets=True, conn=True)`, Task 2·5·7, `kernel.broker.ping.run_checks/failures`, `kernel.cli.boot.config_errors`, `kernel.config.time.KST`.
- Produces: `accounts_app = typer.Typer(help="계좌·모드·연결 키", no_args_is_help=True)` 명령:
  - `show` — `"일시정지: 예|아니오"` 줄 + 계좌마다 `f"{a.id} ({a.env}): {mode} [{src}]{' — ' + note if note else ''}"`(`modes.account_mode`·`mode_source`).
  - `mode <account> <mode> [--confirm LIVE]` — 계좌 없음/`ConfirmationRequired`/`ValueError` → `"오류: …"` stderr + exit 1; 성공 `f"{account}: {mode}"`.
  - `pause` / `resume` — `set_paused` 후 `"일시정지: 예"` / `"일시정지: 아니오"`.
  - `ping [--account ID]` — 옛 `kis ping`: `load_accounts`(전체, 파생 포함) 필터(없으면 "오류: 계좌 id 를 찾을 수 없습니다" exit 1) → `market = build_market(...)`(예외는 `market_error`) → `brokers = build_brokers(secrets, accounts)` → `run_checks(market, brokers, [to_broker_account(a) …], today=datetime.now(KST).date(), market_error=…)` → 줄마다 `"[OK] {label}: {detail}"`/`"[실패] {label}: {detail}"` + `"실패 N건"`, N>0 이면 exit 1.
  - 모든 명령은 `@config_errors`, 부트는 `boot(secrets=True, conn=True)`(`mode`·`show`·`pause`·`resume`) / `boot(secrets=True)`(`ping`). 네 제어 명령은 모두 `load_accounts(secrets=b.secrets)` 를 먼저 읽고(`pause` 도 — 설정 오류를 한 줄로 보고하는 옛 동작 보존) `modes.sync_accounts(conn, accounts)` 를 한 뒤 동작한다. `conn` 은 `finally` 로 닫는다.

- [ ] **Step 1: 실패 테스트** — 옛 `test_cli_control.py` 6건(`exec-key` 제외 → M4) 이식 + ping 2건

```python
# tests/features/accounts/test_cli.py
import yaml
from typer.testing import CliRunner
from trader.kernel.cli.main import app
from trader.kernel.ledger import db
from trader.features.accounts import modes
from trader.features.accounts.settings import load_accounts
from trader.kernel.config.secrets import Secrets

def _accounts(project, extra=()):
    data = {"accounts": [
        {"id": "paper-main", "env": "paper", "cano": "0", "markets": ["KR"], "mode": "auto"},
        {"id": "real-main", "env": "real", "cano": "1", "markets": ["KR"], "mode": "off"}, *extra]}
    (project / "config" / "accounts.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

def _by_id(id_):
    return next(a for a in load_accounts(secrets=Secrets()) if a.id == id_)

def test_accounts_mode_sets_and_shows(project):
    _accounts(project); runner = CliRunner()
    r = runner.invoke(app, ["accounts", "mode", "paper-main", "approve"])
    assert r.exit_code == 0, r.stdout
    conn = db.connect()
    assert modes.get_account_mode(conn, _by_id("paper-main")) == "approve"; conn.close()
    r = runner.invoke(app, ["accounts", "show"])
    assert r.exit_code == 0 and "paper-main" in r.stdout and "approve" in r.stdout and "일시정지" in r.stdout

def test_accounts_mode_real_auto_requires_confirm(project):
    _accounts(project); runner = CliRunner()
    r = runner.invoke(app, ["accounts", "mode", "real-main", "auto"])
    assert r.exit_code == 1 and "LIVE" in (r.stdout + (r.stderr or ""))
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM account_modes").fetchone()[0] == 0; conn.close()
    r = runner.invoke(app, ["accounts", "mode", "real-main", "auto", "--confirm", "LIVE"])
    assert r.exit_code == 0
    conn = db.connect(); assert modes.live_confirmed(conn, "real-main"); conn.close()

def test_accounts_mode_rejects_unknown(project):
    _accounts(project); runner = CliRunner()
    assert runner.invoke(app, ["accounts", "mode", "nope", "auto"]).exit_code == 1
    assert runner.invoke(app, ["accounts", "mode", "paper-main", "fast"]).exit_code == 1

def test_accounts_pause_resume(project):
    _accounts(project); runner = CliRunner()
    assert runner.invoke(app, ["accounts", "pause"]).exit_code == 0
    conn = db.connect(); assert modes.is_paused(conn); conn.close()
    assert runner.invoke(app, ["accounts", "resume"]).exit_code == 0
    conn = db.connect(); assert not modes.is_paused(conn); conn.close()

def test_accounts_show_reports_effective_mode(project):
    _accounts(project)
    conn = db.connect()
    from trader.kernel.registry import discover
    from trader.kernel.registry.contracts import all_migrations
    db.init_schema(conn, all_migrations(discover()))
    conn.execute("INSERT INTO account_modes VALUES ('real-main','auto',NULL,'t')"); conn.commit(); conn.close()
    r = CliRunner().invoke(app, ["accounts", "show"])
    assert r.exit_code == 0, r.stdout
    line = next(x for x in r.stdout.splitlines() if x.startswith("real-main"))
    assert "off" in line and "live_confirmed" in line

def test_accounts_pause_reports_config_error_as_one_line(project):
    (project / "config" / "accounts.yaml").write_text("accounts: [{id: x}]", encoding="utf-8")
    r = CliRunner().invoke(app, ["accounts", "pause"])
    assert r.exit_code == 1 and "오류:" in (r.stdout + (r.stderr or "")) and "Traceback" not in (r.stdout + (r.stderr or ""))

def test_accounts_show_real_fo_account_is_off(project):
    _accounts(project, extra=[{"id": "real-fo", "env": "real", "cano": "2", "acnt_prdt_cd": "03",
                               "markets": ["KR"], "mode": "approve", "asset": "fo"}])
    r = CliRunner().invoke(app, ["accounts", "show"])
    assert r.exit_code == 0, r.stdout
    line = next(x for x in r.stdout.splitlines() if x.startswith("real-fo"))
    assert "off" in line and "approve" not in line.split("—")[0]

def test_ping_counts_missing_keys_as_failures(project):
    _accounts(project)
    r = CliRunner().invoke(app, ["accounts", "ping"])
    assert r.exit_code == 1 and "[실패] 시세 클라이언트" in r.stdout and "paper-main" in r.stdout and "실패 3건" in r.stdout

def test_ping_account_filter(project, monkeypatch):
    _accounts(project)
    from trader.features.accounts import cli as acli
    from trader.kernel.broker.fake import FakeMarket
    monkeypatch.setattr(acli, "build_market", lambda s, a: FakeMarket())
    monkeypatch.setattr(acli, "build_brokers", lambda s, a, **kw: [])
    r = CliRunner().invoke(app, ["accounts", "ping", "--account", "nope"])
    assert r.exit_code == 1 and "찾을 수 없습니다" in (r.stdout + (r.stderr or ""))
    r = CliRunner().invoke(app, ["accounts", "ping", "--account", "paper-main"])
    assert "real-main" not in r.stdout and "[OK] 005930 현재가" in r.stdout and "실패 1건" in r.stdout
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: 구현** — 위 Interfaces. `build_market`·`build_brokers` 는 `from trader.features.accounts.brokers import build_brokers, build_market` 로 모듈 속성에 둔다(monkeypatch 용). `FakeMarket` 의 `is_holiday`·`get_price_kr`·`get_price_us` 가 M1 그대로 동작하는지는 `tests/kernel/broker/test_fake.py` 가 보증.
- [ ] **Step 4: GREEN** — `uv run pytest tests/features/accounts tests/kernel/cli -q`; `tests/kernel/cli/test_main.py::test_help_lists_core_commands` 의 기대 목록에 `"accounts"` 를 더한다(Task 6 에서 `"notify-test"`·`"telegram"` 을 더했으면 그대로 둔다).
- [ ] **Step 5: Commit** — `feat(m2): trader accounts show|mode|pause|resume|ping`

---

### Task 9: `accounts_io.py`·`secrets_io.py` + CLI `add|remove|secrets import`

**Files:**
- Create: `src/trader/features/accounts/accounts_io.py`, `secrets_io.py`
- Modify: `cli.py`(`add`·`remove`·`secrets import`)
- Test: `tests/features/accounts/test_accounts_io.py`, `test_secrets_io.py`

**Interfaces:**
- Consumes: `settings.Account/AccountSpec/load_accounts/CANO_PLACEHOLDER`, `kernel.config.secrets.Secrets/account_prefix/CRED_SUFFIXES/load_secrets`, `kernel.platform.fsutil.atomic_replace`, `kernel.platform.filelock.locked`, `kernel.config.ConfigError`.
- Produces(`accounts_io.py`, 옛 모듈 그대로): `ID_RE`, `AccountInUse(ConfigError)`, `AccountSpec(id, env, asset="stock", markets=["KR"], mode="auto")`, `add_account(path, spec) -> dict{id, backup}`, `blockers(conn, account_id) -> list[str]`, `remove_account(path, account_id, conn) -> dict`. **`blockers` 는 테이블이 있을 때만 조회**(`orders`·`approvals`·`positions_snapshot`·`fo_positions` 는 M4·M5·M10 이 만든다): `_table_exists(conn, name)` 가 False 면 그 사유를 건너뛴다. `dead_status_sql()` 은 옛 `repo.DEAD_STATUSES = ("rejected","cancelled","failed","expired")` 를 **이 모듈 상수 `DEAD_STATUSES`** 로 두고 M4 가 `kernel/execution` 으로 올리며 import 로 바꾼다(README 알려진 차이).
- Produces(`secrets_io.py`, 옛 모듈 그대로): `NAME_MAP`, `import_env(src, dst, *, overwrite=False, keys=None) -> dict`, `CATALOG`, `MAX_VALUE_LEN`, `catalog(accounts) -> list[dict]`, `status(path, accounts) -> dict`, `update(path, accounts, *, values=None, clear=None) -> dict`, `ensure_web_token(path) -> tuple[str, bool]`. `_editing`·`_backup`·`_write` 도 여기(옛 `accounts_io` 가 import 하던 것).
- CLI: `add --id ID --env paper|real [--asset stock|fo] [--markets KR,US] [--mode auto|approve|off]`(출력 `"추가: {id} (백업 {backup})"`), `remove ID`(`AccountInUse`/`ConfigError` → "오류: …" exit 1), `secrets import --from PATH [--overwrite] [--keys A,B]`(출력 4줄 옛 문구 그대로, 값 없음).

- [ ] **Step 1: 실패 테스트** — 옛 `test_accounts_io.py` 전부(테이블 없는 사유 테스트는 테이블을 **직접 CREATE** 하는 fixture 로: `db` fixture 가 `conn` 위에 옛 `orders(account_id, ticker, market, side, qty, order_type, status, mode, created_at)`·`approvals(decision_id, account_id, order_json, status, requested_at, expires_at)`·`positions_snapshot(account_id, date, ticker, market, qty, created_at)`·`fo_positions(account_id, code, kind, contract_side, direction, vehicle, status, created_at)` 최소 열만 만든다 — 테스트 전용, M4 이후 실제 스키마로 바꾼다), `test_secrets_import.py` 4건(CLI 는 `["accounts","secrets","import","--from",…]`), 옛 `tests/web/test_secrets_api.py` 의 `test_ensure_web_token_creates_once`·`test_concurrent_updates_keep_both` 2건(웹 없이 함수 직접), 신규:

```python
# tests/features/accounts/test_accounts_io.py 에 추가
def test_blockers_skip_missing_tables(yml, conn):
    """M4 전: 주문·승인·포지션 테이블이 없으면 그 사유는 보지 않고 삭제를 허용한다."""
    assert accounts_io.blockers(conn, "real-main") == []
    accounts_io.remove_account(yml, "real-main", conn)

# tests/features/accounts/test_secrets_io.py 에 추가
def test_catalog_and_status_never_return_values(project):
    (project / "secrets" / ".env").write_text("KIS_REAL_MAIN_APP_KEY=SECRET-K\nKIS_REAL_MAIN_APP_SECRET=SECRET-S\n", encoding="utf-8")
    accts = [make_account("real-main", "real", "off")]
    st = secrets_io.status(project / "secrets" / ".env", accts)
    assert "SECRET-K" not in str(st) and {k["name"]: k["set"] for k in st["keys"]}["KIS_REAL_MAIN_APP_KEY"]
    assert st["accounts"][0]["uses"] == "전용"

def test_update_rejects_bad_values(project):
    p = project / "secrets" / ".env"; accts = [make_account("p", "paper", "auto")]
    for bad in ("a b", "${X}", "'q", "#c", "x" * 1025):
        with pytest.raises(ConfigError):
            secrets_io.update(p, accts, values={"DART_API_KEY": bad})
    with pytest.raises(ConfigError, match="입력할 수 없는"):
        secrets_io.update(p, accts, values={"WEB_TOKEN": "x"})
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: 구현** — 옛 두 모듈 본문 이식(치환표), `blockers` 에 `_table_exists` 가드, `_probe`·`_verify` 는 `load_accounts(path, secrets=Secrets())`. CLI 3명령 추가.
- [ ] **Step 4: GREEN** — `uv run pytest tests/features/accounts -q`.
- [ ] **Step 5: Commit** — `feat(m2): accounts_io·secrets_io — 계좌 추가/삭제·.env 가져오기/연결 키 입력, CLI add|remove|secrets import`

---

### Task 10: `features/accounts/telegram.py` — `/pause`·`/resume`·`/mode` + 상태 절, `api.py`, `feature.py` 완성

**Files:**
- Create: `src/trader/features/accounts/telegram.py`
- Modify: `feature.py`(`telegram_commands=TELEGRAM_COMMANDS, status_sections=(STATUS_SECTION,), control_namespaces=("paused",)`), `api.py`
- Test: `tests/features/accounts/test_telegram.py`, `tests/features/accounts/test_feature.py`

**Interfaces:**
- Consumes: Task 4 `CommandContext`, Task 5 `modes`, Task 2 `load_accounts`, `kernel.notify.messages`.
- Produces:
  - `TELEGRAM_COMMANDS = (TelegramCommand("pause", False, "/pause", pause), TelegramCommand("resume", False, "/resume", resume), TelegramCommand("mode", False, "/mode <계좌> <auto|approve|off>", mode))` — 본문은 옛 `_control_command` 의 pause/resume/mode 분기 그대로(실전은 off 로만, 안내 문구에 `` `trader accounts mode <계좌> approve|auto --confirm LIVE` ``). 계좌는 `load_accounts()`(현물+파생 전부).
  - `STATUS_SECTION = StatusSection("계좌", render)` — `render(ctx) -> [f"{paused} · 계좌 모드: {modes}"]`, `paused` 는 `"일시정지"|"실행 중"`, 각 계좌 `f"{a.id}({ENV[a.env]}) {MODE[mode]}" + (" · 강등" if note else "")`(옛 `_mode_label`).
  - `api.py`: `load_accounts, split_accounts, to_broker_account, Account, Mode, account_mode, effective_mode, get_account_mode, is_paused, LIVE_CONFIRM, ConfirmationRequired, market_creds, build_brokers, build_market` 재export + `__all__`.

- [ ] **Step 1: 실패 테스트** — 옛 `test_commands.py` 의 `test_telegram_pause_resume_mode_watch`(watch 부분 제외 → M7), `test_real_auto_via_telegram_rejected`, `test_status_shows_effective_mode`, `test_mode_and_status_reach_fo_accounts`(현물·파생을 accounts.yaml 에 적어 `load_accounts` 가 읽게) + feature 계약

```python
# tests/features/accounts/test_telegram.py
import yaml
from datetime import UTC, datetime
from trader.kernel.notify import commands
from trader.kernel.notify.telegram import Update
from trader.kernel.registry import discover
from trader.features.accounts import modes
from trader.features.accounts.settings import load_accounts
from trader.kernel.config.secrets import Secrets

class Sent:
    def __init__(self): self.msgs = []
    def send(self, text, *, escape=True): self.msgs.append(text); return True

class Recv:
    last_update_id = None
    def __init__(self, texts): self.texts = texts
    def poll(self, offset=None): return [Update(update_id=i + 1, text=t, chat_id="1") for i, t in enumerate(self.texts)]

def _write(project, extra=()):
    data = {"accounts": [
        {"id": "paper-main", "env": "paper", "cano": "0", "markets": ["KR"], "mode": "auto"},
        {"id": "real-main", "env": "real", "cano": "1", "markets": ["KR"], "mode": "off"}, *extra]}
    (project / "config" / "accounts.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

def _ctx(conn, sent):
    return commands.CommandContext(conn=conn, settings=None, state=None, now=lambda: datetime.now(UTC), notifier=sent, features=discover())

def _acct(id_):
    return next(a for a in load_accounts(secrets=Secrets()) if a.id == id_)

def test_telegram_pause_resume_mode(project, conn):
    _write(project); sent = Sent(); ctx = _ctx(conn, sent)
    r = commands.poll_once(ctx, Recv(["/pause"]))
    assert r["handled"] == 1 and modes.is_paused(conn)
    commands.poll_once(ctx, Recv(["/resume"])); assert not modes.is_paused(conn)
    commands.poll_once(ctx, Recv(["/mode paper-main approve"]))
    assert modes.get_account_mode(conn, _acct("paper-main")) == "approve"
    commands.poll_once(ctx, Recv(["/mode nope auto", "/mode paper-main fast"]))
    assert any("사용법" in m or "알 수 없는" in m for m in sent.msgs[-2:])

def test_real_auto_via_telegram_rejected(project, conn):
    _write(project); sent = Sent(); ctx = _ctx(conn, sent)
    commands.poll_once(ctx, Recv(["/mode real-main auto"]))
    assert modes.get_account_mode(conn, _acct("real-main")) == "off"
    assert "거부" in sent.msgs[-1] and "accounts mode" in sent.msgs[-1]
    commands.poll_once(ctx, Recv(["/mode real-main approve"]))
    assert modes.get_account_mode(conn, _acct("real-main")) == "off" and "거부" in sent.msgs[-1]
    modes.set_account_mode(conn, _acct("real-main"), "approve")
    commands.poll_once(ctx, Recv(["/mode real-main off"]))
    assert modes.get_account_mode(conn, _acct("real-main")) == "off"

def test_status_shows_effective_mode(project, conn):
    _write(project); sent = Sent(); ctx = _ctx(conn, sent)
    conn.execute("INSERT INTO account_modes VALUES ('real-main','auto',NULL,'t')"); conn.commit()
    commands.poll_once(ctx, Recv(["/status"]))
    msg = sent.msgs[-1]
    assert "real-main(실전) 끔" in msg and "강등" in msg and "real-main(실전) 자동" not in msg and "실행 중" in msg

def test_mode_and_status_reach_fo_accounts(project, conn):
    _write(project, extra=[
        {"id": "paper-fo", "env": "paper", "cano": "0", "acnt_prdt_cd": "03", "markets": ["KR"], "mode": "auto", "asset": "fo"},
        {"id": "real-fo", "env": "real", "cano": "0", "acnt_prdt_cd": "03", "markets": ["KR"], "mode": "approve", "asset": "fo"}])
    sent = Sent(); ctx = _ctx(conn, sent)
    commands.poll_once(ctx, Recv(["/mode paper-fo off"]))
    assert modes.get_account_mode(conn, _acct("paper-fo")) == "off" and "알 수 없는" not in sent.msgs[-1]
    commands.poll_once(ctx, Recv(["/status"]))
    msg = sent.msgs[-1]
    assert "paper-fo(모의) 끔" in msg and "real-fo(실전) 끔" in msg and "real-fo(실전) 승인" not in msg
```

```python
# tests/features/accounts/test_feature.py
from trader.features.accounts.feature import FEATURE
from trader.features.accounts import api
from trader.kernel.registry import check, discover

def test_feature_declaration():
    assert FEATURE.id == "accounts" and FEATURE.tier == "service"
    assert [s.key for s in FEATURE.sections] == ["accounts"]
    assert [m.version for m in FEATURE.migrations] == [2] and FEATURE.migrations[0].owner == "accounts"
    assert {c.name for c in FEATURE.telegram_commands} == {"pause", "resume", "mode"}
    assert all(not c.untrusted_ok for c in FEATURE.telegram_commands)
    assert [s.title for s in FEATURE.status_sections] == ["계좌"] and FEATURE.control_namespaces == ("paused",)
    assert FEATURE.cli is not None and FEATURE.routes == ()  # 웹 자원은 M11

def test_discovered_and_contracts_pass():
    feats = discover()
    assert "accounts" in [f.id for f in feats] and check(feats) == []

def test_api_surface():
    for name in ("load_accounts", "split_accounts", "to_broker_account", "Account", "Mode", "account_mode",
                 "effective_mode", "get_account_mode", "is_paused", "LIVE_CONFIRM", "ConfirmationRequired",
                 "market_creds", "build_brokers", "build_market"):
        assert name in api.__all__ and hasattr(api, name)
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: 구현.**
- [ ] **Step 4: GREEN** — `uv run pytest -q` 전체 + `uv run ruff check src tests`.
- [ ] **Step 5: Commit** — `feat(m2): accounts 텔레그램 명령(/pause /resume /mode)·상태 절·api 공개면·FEATURE 완성`

---

### Task 11: 문서·체크리스트·공개면 점검

**Files:**
- Modify: `src/trader/features/accounts/README.md`(완성), `docs/architecture.md`(`## notify` 절 신설, `## config` 절에 `secrets` 공개면 한 줄, `## features 층 (예정)` → `## features 층` 에 accounts 한 줄), `docs/TODO.md`(M2 체크·이연 이관), `CLAUDE.md`(실행 명령 표에 `trader accounts …`·`notify-test`·`telegram poll|loop` 3줄, 금지사항 "같은 헬퍼" 줄에 `secrets.load_secrets`·`notify.messages` 정본 추가, 불변식 "실전 auto 는 LIVE 확인" 의 "(M2 에서 구현)" 표기 제거 — 16KB 이내), `src/trader/kernel/notify/__init__.py`(`__all__`)
- Test: `tests/test_docs.py`(기존), `tests/kernel/notify/test_public_api.py`

**Interfaces:** 없음(문서).

- [ ] **Step 1: 실패 테스트**

```python
# tests/kernel/notify/test_public_api.py
from pathlib import Path

def test_notify_public_surface():
    import trader.kernel.notify as n
    for name in ("Notifier", "NullNotifier", "TelegramNotifier", "TelegramReceiver", "Update", "parse_command",
                 "make_notifier", "make_receiver", "CommandContext", "poll_once", "messages"):
        assert name in n.__all__ and hasattr(n, name)

def test_architecture_has_notify_section_and_accounts_readme():
    text = Path("docs/architecture.md").read_text(encoding="utf-8")
    assert "## notify" in text and "notify.enabled" in text and "알려진 차이" in text
    readme = Path("src/trader/features/accounts/README.md").read_text(encoding="utf-8")
    assert "account_modes" in readme and "LIVE" in readme and "알려진 차이" in readme
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: 작성** — `README.md` 4항목(책임 / 공개면 `api.py` / 규약 / 테스트) + 알려진 차이: ① `control.mode:*`·`live_confirmed:*` → `account_modes` 테이블(스펙 §2.4) ② CLI `control` → `accounts`, `kis ping` → `accounts ping`, `secrets import` → `accounts secrets import` ③ `/watch`·watchlist 는 M7(scan) ④ `/approve`·`/reject`·`/run`·`/kill`·`/daily`·`/fo` 는 각 M(M4·M7·M6·M8·M10), `/status` 의 데몬·국면·스캔·미승인 줄도 해당 M 이 `status_sections` 로 더한다 ⑤ `exec-key list|release` 는 M4 ⑥ `blockers` 는 테이블 없으면 그 사유 생략, `DEAD_STATUSES` 는 M4 가 커널로 ⑦ 체결통보 리스너(`_notice_listeners`)는 M5 ⑧ `ensure_web_token`·연결 키 웹 라우트는 M11 ⑨ `parse_command("/foo")` 가 이름을 돌려준다(거부는 디스패처). `architecture.md` `## notify`: 책임(발신·outbox·수신·디스패처·문구 베이스) / 공개면 / 규약(토큰 로그 금지, 4000자 분할·HTML 이스케이프, outbox 보관 성공=True·`flush_outbox` 가 재전송·24h·10건, 신뢰 발신자 규칙, 오프셋 업데이트마다 전진, 모르는 명령 분당 1회 도움말, `/status` 내장 + `status_sections`, `notify.enabled=false` 면 NullNotifier·수신기 없음(리허설), 명령 이름 `status` 예약) / 알려진 차이(`expire_approvals` 훅 M4, 데몬 `telegram_poll`·outbox flush 잡 M4, 옛 `telegram` 로거 → `trader.notify`). `TODO.md` M2 절을 Task 1~11 체크로 바꾸고 M1 의 "PR·CI·머지" 체크, "이관" 줄 삭제; M4 이연에 "`expire_approvals` 훅·데몬 `telegram_poll` 잡·`exec-key` CLI·`DEAD_STATUSES` 커널 이동·`accounts_io.blockers` 실제 스키마", M7 이연에 "watchlist·`/watch`(옛 `test_control` watchlist 2건)", M11 이연에 "`ensure_web_token` 연결·`/api/accounts`·연결 키 라우트" 추가.
- [ ] **Step 4: GREEN** — `uv run pytest -q`, `uv run ruff check src tests`, `cd web && npm run typecheck && npm test -- --run`(변화 없음), `uv run trader dev gen-sse-keys --check`, `wc -c CLAUDE.md` ≤ 16384. scratch `TRADER_HOME` 에서 `uv run trader init && uv run trader selfcheck && uv run trader accounts show && uv run trader notify-test` 가 돈다(계좌 템플릿 복사·`features: ["accounts"]`·`ledger_version: 2`).
- [ ] **Step 5: Commit** — `docs(m2): accounts README·architecture notify 절·TODO·CLAUDE 명령 표`

---

## 완료 기준(PR 머지 조건)

- 옛 테스트 이식 완료: `test_control.py`(모드 4건), `test_accounts_io.py`(21건), `test_secrets_import.py`(4건), `test_multi_account_keys.py`(creds·brokers·market 5건; `_notice_listeners`·토큰 캐시 2건은 M5/M1 범위), `test_cli_control.py`(6건, `exec-key` 제외), `test_cli_telegram_loop.py`(1건), `notify/test_telegram.py`(12건), `notify/test_telegram_receiver.py`(6건), `notify/test_commands.py`(9건 — watch·approve·run·kill·daily·status 데몬줄 제외), `test_config.py`(accounts 4건·secrets 5건), `test_messages.py`(베이스 1건). 건수는 PR 본문에 옛 대비 표로.
- 바꾼 옛 단언: `parse_command("/foo")` 1건만(PR 본문 명시).
- `uv run pytest -q` 녹색, CI ubuntu+windows 녹색, `ruff` 0, `import_violations` 0, `CLAUDE.md` ≤ 16KB, `check(discover()) == []`.
- `templates/config/accounts.yaml` 에 자리표시자만, `secrets/`·`config/` 커밋 없음(`git status` 로 확인).
