import io
import sys
import threading

import pytest
import typer
from typer.testing import CliRunner

from trader.kernel.cli import boot as boot_mod
from trader.kernel.cli import main as main_mod
from trader.kernel.cli.main import app
from trader.kernel.config import ConfigError
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.platform.paths import ProjectRootNotFound
from trader.kernel.registry.types import Feature


def test_help_lists_core_commands():
    r = CliRunner().invoke(app, ["--help"])
    assert r.exit_code == 0
    names = ("init", "selfcheck", "daemon", "web", "dev", "notify-test", "telegram", "accounts")
    assert all(c in r.stdout for c in names)


def test_config_error_exits_1_with_korean_prefix(project):
    (project / "config" / "settings.yaml").write_text("bogus: 1\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["web"])
    assert r.exit_code == 1
    assert "오류: " in r.output and "bogus" in r.output


def test_config_errors_decorator_handles_root_not_found():
    @boot_mod.config_errors
    def cmd():
        raise ProjectRootNotFound("루트 없음")

    t = typer.Typer()
    t.command()(cmd)
    r = CliRunner().invoke(t, [])
    assert r.exit_code == 1 and "오류: 루트 없음" in r.output


def test_boot_contract_violation_is_config_error(project, monkeypatch):
    monkeypatch.setattr(boot_mod, "discover", lambda: [])
    monkeypatch.setattr(boot_mod, "check", lambda fs: ["위반 하나", "위반 둘"])
    with pytest.raises(ConfigError, match="위반 하나\n위반 둘"):
        boot_mod.boot()


def test_boot_loads_settings_and_conn(project):
    b = boot_mod.boot(settings=True, conn=True)
    try:
        assert b.settings.markets == ["KR", "US"]
        assert b.conn is not None
        assert [f.id for f in b.features] == [
            "accounts",
            "briefing",
            "evaluation",
            "fo",
            "ingest",
            "ml",
            "quotes",
            "regime",
            "saveticker",
            "scalp",
            "scan",
            "spot",
        ]
    finally:
        b.conn.close()


def test_boot_default_is_light(project):
    b = boot_mod.boot()
    assert b.settings is None and b.conn is None


def test_feature_cli_is_mounted(monkeypatch):
    sub = typer.Typer()

    @sub.command()
    def hello():
        typer.echo("hi")

    feat = Feature(id="demo", tier="lane", cli=sub)
    root = main_mod.build_app([feat])
    r = CliRunner().invoke(root, ["demo", "hello"])
    assert r.exit_code == 0 and "hi" in r.stdout


def test_build_daemon_deps(project):
    b = boot_mod.boot(ports=True, schedule=True)
    try:
        deps = main_mod.build_daemon_deps(b, dry_run=True)
        assert deps.dry_run is True
        assert deps.markets == ["KR", "US"]
        assert set(deps.gates) == {"market", "monitor"}
        assert deps.ports is b.ports and "accounts" in deps.ports
        assert isinstance(deps.stop_event, threading.Event)
        assert deps.state == project.resolve() / "state"
        assert deps.now().tzinfo is not None
        assert isinstance(deps.notifier, NullNotifier)  # 시크릿 없음
        assert deps.registry is b.registry
        assert set(deps.hooks) == {
            "news.post_ingest",
            "daemon.bootstrap",
            "scan_analyze.post",
        }  # scan 사건 감지·ml 백필·evaluation 섀도 기록
        assert deps.secrets is not None
    finally:
        b.conn.close()


def test_build_web_deps(project, monkeypatch):
    monkeypatch.setenv("WEB_TOKEN", "tok")
    b = boot_mod.boot(settings=True, conn=True)
    try:
        deps = main_mod.build_web_deps(b)
        assert deps.token == "tok"
        assert deps.dist == project.resolve() / "web" / "dist"
        assert deps.features == b.features
        assert deps.ports == b.ports
    finally:
        b.conn.close()


def test_build_web_deps_excludes_execution_ports(project, monkeypatch):
    """웹은 원장+큐만 쓴다(T3-R3): 주문 집행 포트(`stock_exec`, `execute`·주문 호출을 가진
    포트)는 웹 deps 에서 빠지고 나머지 포트는 그대로 넘어간다."""
    from dataclasses import replace
    from types import SimpleNamespace

    class _Exec:
        def execute(self, *a):
            return None

    class _Orders:
        def place_order(self, *a):
            return None

    accounts = SimpleNamespace(accounts=lambda: [])
    b = boot_mod.boot(settings=True, conn=True)
    try:
        b = replace(b, ports={
            "accounts": accounts, "stock_exec": SimpleNamespace(), "exec2": _Exec(),
            "orders2": _Orders(), "ml": SimpleNamespace(close_table=lambda: None),
        })  # fmt: skip
        deps = main_mod.build_web_deps(b)
        assert set(deps.ports) == {"accounts", "ml"}
        assert deps.ports["accounts"].accounts() == []
    finally:
        b.conn.close()


def test_build_web_deps_wraps_accounts_port_read_only(project, monkeypatch):
    """웹 accounts 포트는 읽기 전용 래퍼(I2): 계좌 목록·일시정지·정책만 위임하고 브로커(주문
    경로)·시세 클라이언트는 노출하지 않는다."""
    from dataclasses import replace
    from types import SimpleNamespace

    class _Port:
        def accounts(self):
            return ["spot-1"]

        def fo_accounts(self):
            return ["fo-1"]

        def paused(self, conn):
            return True

        def policy(self, conn, account):
            return ("approve", None)

        def brokers(self):
            raise AssertionError("웹이 브로커를 만들면 안 된다")

        def fo_brokers(self):
            raise AssertionError("웹이 파생 브로커를 만들면 안 된다")

        def market_client(self):
            raise AssertionError("웹은 계좌 포트로 시세 클라이언트를 만들지 않는다")

    b = boot_mod.boot(settings=True, conn=True)
    try:
        deps = main_mod.build_web_deps(replace(b, ports={"accounts": _Port()}))
        port = deps.ports["accounts"]
        assert not hasattr(port, "brokers") and not hasattr(port, "fo_brokers")
        assert not hasattr(port, "market_client")
        assert port.accounts() == ["spot-1"] and port.fo_accounts() == ["fo-1"]
        assert port.paused(None) is True and port.policy(None, "spot-1") == ("approve", None)
        # 현물 전용 포트(fo_accounts 없음)는 래퍼에서도 없어 라우트의 getattr 분기가 그대로 산다
        deps = main_mod.build_web_deps(
            replace(b, ports={"accounts": SimpleNamespace(accounts=lambda: [])})
        )
        assert getattr(deps.ports["accounts"], "fo_accounts", None) is None
    finally:
        b.conn.close()


def test_daemon_exits_1_when_lock_not_acquired(project, monkeypatch):
    class FakeDaemon:
        def __init__(self, features, deps):
            pass

        def start(self, **kw):
            return False

        def shutdown(self, wait=True):
            raise AssertionError("시작 못 했으면 shutdown 없음")

    monkeypatch.setattr(main_mod, "Daemon", FakeDaemon)
    r = CliRunner().invoke(app, ["daemon", "--dry-run"])
    assert r.exit_code == 1
    assert "다른 데몬이 실행 중" in r.output


def test_daemon_blocks_then_shuts_down(project, monkeypatch):
    calls = []

    class FakeDaemon:
        def __init__(self, features, deps):
            self.deps = deps

        def start(self, **kw):
            calls.append("start")
            self.deps.stop_event.set()
            return True

        def shutdown(self, wait=True):
            calls.append("shutdown")

    monkeypatch.setattr(main_mod, "Daemon", FakeDaemon)
    r = CliRunner().invoke(app, ["daemon"])
    assert r.exit_code == 0, r.output
    assert calls == ["start", "shutdown"]


def test_web_runs_uvicorn_with_options(project, monkeypatch):
    seen = {}
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda a, **kw: seen.update(app=a, **kw))
    r = CliRunner().invoke(app, ["web", "--port", "9999"])
    assert r.exit_code == 0, r.output
    assert seen["host"] == "127.0.0.1" and seen["port"] == 9999
    assert seen["log_config"] is None and seen["app"] is not None


def test_force_utf8_reconfigures_and_tolerates_none(monkeypatch):
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", None)
    main_mod.force_utf8()
    assert out.encoding == "utf-8"


def test_app_call_forces_utf8(monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod, "force_utf8", lambda: calls.append(1))
    with pytest.raises(SystemExit):
        app(["--help"])
    assert calls == [1]


def test_web_uses_settings_web_port_and_flag_wins(project, monkeypatch):
    (project / "config" / "settings.yaml").write_text("web:\n  port: 9999\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda a, **kw: seen.update(kw))
    assert CliRunner().invoke(app, ["web"]).exit_code == 0
    assert seen["port"] == 9999 and seen["host"] == "127.0.0.1"
    assert CliRunner().invoke(app, ["web", "--port", "8000"]).exit_code == 0
    assert seen["port"] == 8000


def test_registry_error_exits_1_with_korean_prefix(project, monkeypatch):
    from trader.kernel.registry import RegistryError

    def bad():
        raise RegistryError("x")

    monkeypatch.setattr(boot_mod, "discover", bad)
    r = CliRunner().invoke(app, ["web"])
    assert r.exit_code == 1 and "오류: x" in r.output


def test_boot_default_log_file_names(project):
    from trader.kernel.platform import logging_setup

    logs = project / "state" / "logs"
    try:
        boot_mod.boot()
        assert (logs / "trader.log").exists() and not (logs / "trader-trader.log").exists()
        boot_mod.boot(log_name="web")
        assert (logs / "trader-web.log").exists()
    finally:
        logging_setup.reset()


def test_build_registry_includes_feature_sections():
    assert boot_mod.build_registry([]) is not None


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
