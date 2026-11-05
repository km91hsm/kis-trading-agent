"""`trader app`: 한 프로세스에서 데몬(스케줄러) + 웹(uvicorn 스레드) + 브라우저/트레이.

실제 데몬·서버·브라우저·트레이는 띄우지 않는다 — 가짜 Daemon·가짜 uvicorn Server·webbrowser 패치."""

import socket
import threading

import pytest
from typer.testing import CliRunner

from trader.kernel.cli import app_cmd
from trader.kernel.cli.main import app
from trader.kernel.platform.tray import TrayUnavailable


class FakeServer:
    """uvicorn.Server 흉내: should_exit 가 설 때까지 run() 이 블로킹한다."""

    instances: list = []

    def __init__(self, config):
        self.config = config
        self.should_exit = False
        self.ran = threading.Event()
        FakeServer.instances.append(self)

    def run(self):
        self.ran.set()
        while not self.should_exit:
            threading.Event().wait(0.01)


class FakeConfig:
    def __init__(self, app, **kw):
        self.app = app
        self.kw = kw


def _fake_daemon(calls: list, *, ok: bool = True):
    class FakeDaemon:
        def __init__(self, features, deps):
            self.deps = deps
            calls.append(("init", None))

        def start(self, **kw):
            calls.append(("start", kw))
            return ok

        def shutdown(self, wait=True):
            calls.append(("shutdown", wait))

    return FakeDaemon


@pytest.fixture
def fakes(project, monkeypatch):
    import uvicorn

    FakeServer.instances = []
    calls: list = []
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(app_cmd, "wait_for_port", lambda *a, **kw: True)  # 포트가 열린 것으로

    orig_stop = app_cmd.WebThread.stop

    def stop(self, *a, **kw):
        calls.append(("web_stop", None))
        return orig_stop(self, *a, **kw)

    monkeypatch.setattr(app_cmd.WebThread, "stop", stop)
    return calls


def test_app_runs_daemon_and_opens_browser(fakes, monkeypatch):
    calls = fakes
    holder = {}

    class Daemon(_fake_daemon(calls)):
        def __init__(self, features, deps):
            super().__init__(features, deps)
            holder["d"] = self

    seen = {}

    def fake_open(url):
        seen["url"] = url
        holder["d"].deps.stop_event.set()  # 브라우저가 열린 뒤 종료(Ctrl+C 흉내)
        return True

    monkeypatch.setattr(app_cmd, "Daemon", Daemon)
    monkeypatch.setattr("webbrowser.open", fake_open)
    r = CliRunner().invoke(app, ["app", "--browser-delay", "0"])
    assert r.exit_code == 0, r.output
    assert seen["url"] == "http://127.0.0.1:8787"
    assert ("start", {"install_signals": False}) in calls
    server = FakeServer.instances[0]
    assert server.config.kw["host"] == "127.0.0.1" and server.config.kw["port"] == 8787
    assert server.config.kw["log_config"] is None and server.should_exit is True
    names = [c[0] for c in calls]
    assert names.index("web_stop") < names.index("shutdown")
    assert ("shutdown", True) in calls


def test_app_tray_runs_daemon_in_thread_and_quits(fakes, monkeypatch):
    """--tray: 데몬은 시그널을 설치하지 않고(스케줄러 스레드), 메인 스레드는 트레이. 종료 메뉴가
    웹 → 데몬(wait=True) 순으로 내리고 나서야 명령이 돌아온다."""
    calls = fakes
    monkeypatch.setattr(app_cmd, "Daemon", _fake_daemon(calls))
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    seen = {}

    def fake_tray(**kw):
        seen["thread"] = threading.current_thread().name
        seen["kw"] = kw
        FakeServer.instances[0].ran.wait(2)
        kw["on_quit"]()
        calls.append(("tray_done", None))

    monkeypatch.setattr(app_cmd, "run_tray", fake_tray)
    r = CliRunner().invoke(app, ["app", "--tray", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert seen["thread"] == "MainThread"
    assert ("start", {"install_signals": False}) in calls
    names = [c[0] for c in calls]
    assert names.index("web_stop") < names.index("shutdown") < names.index("tray_done")
    assert names.count("shutdown") == 1 and ("shutdown", True) in calls
    kw = seen["kw"]
    assert {"state_fn", "on_toggle_pause", "on_open_web", "on_open_logs", "icon_dir"} <= set(kw)
    assert kw["stop_event"] is not None


def test_app_tray_toggle_pause_uses_accounts_port_with_tray_channel(fakes, monkeypatch):
    calls = fakes
    monkeypatch.setattr(app_cmd, "Daemon", _fake_daemon(calls))
    seen = {}

    def fake_tray(**kw):
        kw["on_toggle_pause"]()
        kw["on_quit"]()

    import trader.features.accounts.modes as modes

    real = modes.set_paused

    def spy(conn, value, *, channel="cli", actor=None):
        seen["channel"] = channel
        seen["value"] = value
        real(conn, value, channel=channel, actor=actor)

    monkeypatch.setattr(modes, "set_paused", spy)
    monkeypatch.setattr(app_cmd, "run_tray", fake_tray)
    r = CliRunner().invoke(app, ["app", "--tray", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert seen == {"channel": "tray", "value": True}


def test_app_tray_falls_back_without_pystray(fakes, monkeypatch):
    calls = fakes
    holder = {}

    class Daemon(_fake_daemon(calls)):
        def __init__(self, features, deps):
            super().__init__(features, deps)
            holder["d"] = self

    def no_tray(**kw):
        # 트레이가 없어도 데몬·웹은 계속 — 일반 모드 대기 중 종료 요청
        threading.Timer(0.2, holder["d"].deps.stop_event.set).start()
        raise TrayUnavailable("pystray 없음")

    monkeypatch.setattr(app_cmd, "Daemon", Daemon)
    monkeypatch.setattr(app_cmd, "run_tray", no_tray)
    r = CliRunner().invoke(app, ["app", "--tray", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert "트레이" in r.output
    assert ("shutdown", True) in calls


def test_app_exits_1_when_daemon_fails_early(fakes, monkeypatch):
    calls = fakes
    monkeypatch.setattr(app_cmd, "Daemon", _fake_daemon(calls, ok=False))

    def must_not_run(**kw):
        raise AssertionError("트레이가 떠선 안 된다")

    monkeypatch.setattr(app_cmd, "run_tray", must_not_run)
    r = CliRunner().invoke(app, ["app", "--tray", "--no-browser"])
    assert r.exit_code == 1
    assert "다른 데몬이 실행 중입니다" in r.output
    assert FakeServer.instances == []  # 웹도 띄우지 않는다
    assert "shutdown" not in [c[0] for c in calls]


def _hold_lock(project):
    import json
    import os

    from trader.kernel.daemon import heartbeat

    (project / "state" / heartbeat.LOCK_NAME).write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    )


def test_app_lock_held_opens_browser_and_exits_0(project, fakes, monkeypatch):
    """다른 인스턴스가 잠금을 쥐고 있으면 데몬·웹을 띄우지 않고 브라우저만 연 뒤 0 (FR-1·FR-2)."""
    calls = fakes
    _hold_lock(project)
    monkeypatch.setattr(app_cmd, "Daemon", _fake_daemon(calls))
    seen = []
    monkeypatch.setattr("webbrowser.open", lambda url: seen.append(url) or True)
    r = CliRunner().invoke(app, ["app"])
    assert r.exit_code == 0, r.output
    assert "이미 실행 중입니다 — http://127.0.0.1:8787" in r.output
    assert seen == ["http://127.0.0.1:8787"]
    assert calls == [] and FakeServer.instances == []


def test_app_lock_held_no_browser_exits_0_without_opening(project, fakes, monkeypatch):
    calls = fakes
    _hold_lock(project)
    monkeypatch.setattr(app_cmd, "Daemon", _fake_daemon(calls))
    seen = []
    monkeypatch.setattr("webbrowser.open", lambda url: seen.append(url) or True)
    r = CliRunner().invoke(app, ["app", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert "이미 실행 중입니다" in r.output
    assert seen == [] and calls == [] and FakeServer.instances == []


def test_wait_for_port_returns_when_listening_and_gives_up():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert app_cmd.wait_for_port("127.0.0.1", port, timeout_s=2.0) is True
    finally:
        srv.close()
    assert app_cmd.wait_for_port("127.0.0.1", port, timeout_s=0.3) is False
    stop = threading.Event()
    stop.set()
    assert app_cmd.wait_for_port("127.0.0.1", port, timeout_s=5.0, stop=stop) is False


def test_app_tray_quit_menu_waits_for_daemon_shutdown(fakes, monkeypatch):
    """실제 run_tray 처럼 on_quit 은 워커(daemon) 스레드에서 돌고, 트레이는 `finished` 를 보면
    아이콘을 내린다. 정상 종료(웹 stop) 로는 `finished` 가 서지 않고, run_app 은 데몬
    shutdown(wait=True) 이 끝난 뒤에야 돌아온다."""
    calls = fakes
    shutdown_done = threading.Event()

    class SlowDaemon(_fake_daemon(calls)):
        def shutdown(self, wait=True):
            calls.append(("shutdown", wait))
            threading.Event().wait(0.5)  # 실행 중 잡을 기다리는 동안
            shutdown_done.set()

    seen = {}

    def fake_tray(**kw):
        icon_stopped = threading.Event()

        def work():
            try:
                kw["on_quit"]()
            finally:
                icon_stopped.set()

        threading.Thread(target=work, daemon=True).start()
        while not icon_stopped.is_set():
            if kw["finished"].is_set():  # 데몬 스레드 종료로 보고 아이콘을 내림
                seen["finished_early"] = True
                return
            threading.Event().wait(0.01)

    monkeypatch.setattr(app_cmd, "Daemon", SlowDaemon)
    monkeypatch.setattr(app_cmd, "run_tray", fake_tray)
    r = CliRunner().invoke(app, ["app", "--tray", "--no-browser"])
    assert shutdown_done.is_set(), "run_app 이 데몬 shutdown 완료 전에 돌아왔다"
    assert r.exit_code == 0, r.output
    assert "finished_early" not in seen
    assert [c for c in calls if c[0] == "shutdown"] == [("shutdown", True)]


class CrashingServer(FakeServer):
    def run(self):
        self.ran.set()
        raise SystemExit(1)  # uvicorn 은 포트 점유 시 sys.exit(1)


def test_app_exits_1_when_web_server_dies(fakes, monkeypatch):
    import uvicorn

    calls = fakes
    monkeypatch.setattr(uvicorn, "Server", CrashingServer)
    monkeypatch.setattr(app_cmd, "Daemon", _fake_daemon(calls))
    r = CliRunner().invoke(app, ["app", "--no-browser"])
    assert r.exit_code == 1
    assert "웹 UI 서버가 멈췄습니다" in r.stderr
    assert ("shutdown", True) in calls


def test_app_tray_exits_1_when_web_server_dies(fakes, monkeypatch):
    import uvicorn

    calls = fakes
    monkeypatch.setattr(uvicorn, "Server", CrashingServer)
    monkeypatch.setattr(app_cmd, "Daemon", _fake_daemon(calls))
    seen = {}

    def fake_tray(**kw):
        seen["finished"] = kw["finished"].wait(3)  # 웹 사망 → 아이콘을 내리고 돌아온다

    monkeypatch.setattr(app_cmd, "run_tray", fake_tray)
    r = CliRunner().invoke(app, ["app", "--tray", "--no-browser"])
    assert r.exit_code == 1
    assert seen["finished"] is True
    assert ("shutdown", True) in calls
