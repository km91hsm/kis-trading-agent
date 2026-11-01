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
from trader.kernel.platform.paths import ProjectRootNotFound
from trader.kernel.registry.types import Feature


def test_help_lists_core_commands():
    r = CliRunner().invoke(app, ["--help"])
    assert r.exit_code == 0
    assert all(c in r.stdout for c in ("init", "selfcheck", "daemon", "web", "dev"))


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
        assert b.features == []
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
    b = boot_mod.boot(settings=True, conn=True, schedule=True)
    try:
        deps = main_mod.build_daemon_deps(b, dry_run=True)
        assert deps.dry_run is True
        assert deps.markets == ["KR", "US"]
        assert deps.gates == {}
        assert isinstance(deps.stop_event, threading.Event)
        assert deps.state == project.resolve() / "state"
        assert deps.now().tzinfo is not None
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
