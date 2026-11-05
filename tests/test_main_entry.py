"""`python -m trader` / 번들 진입점: 더블클릭 → `trader app`, 창 없는 TraderApp → `app --tray`."""

import sys

import typer

from trader import __main__ as entry
from trader.kernel.cli import main as main_mod
from trader.kernel.platform import bundle


def test_main_double_click_routes_to_app(monkeypatch):
    calls = []
    monkeypatch.setattr(bundle, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", "/x/dist/trader/trader")
    monkeypatch.setattr(sys, "argv", ["trader"])
    monkeypatch.setattr(main_mod, "app", lambda args=None: calls.append(args))
    entry.main()
    assert calls == [["app"]]
    monkeypatch.setattr(sys, "argv", ["trader", "status"])
    entry.main()
    assert calls[-1] is None  # 인자가 있으면 보통 CLI


def test_main_traderapp_routes_to_tray(monkeypatch):
    calls: list[list[str] | None] = []
    monkeypatch.setattr(main_mod, "app", lambda args=None: calls.append(args))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["TraderApp"])
    monkeypatch.setattr(sys, "executable", "/x/dist/trader/TraderApp")
    entry.main()
    monkeypatch.setattr(sys, "executable", "/x/dist/trader/trader")
    entry.main()
    assert calls == [["app", "--tray"], ["app"]]


def test_main_double_click_pauses_on_error_in_windows_console(monkeypatch):
    """Windows 더블클릭은 오류로 끝나면 콘솔이 바로 닫혀 메시지를 못 본다 → 종료 전 Enter 대기."""
    monkeypatch.setattr(bundle, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", "C:/x/trader/trader.exe")
    monkeypatch.setattr(sys, "argv", ["trader"])
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    def failing_app(args=None):
        raise typer.Exit(code=1)

    waited = []
    monkeypatch.setattr(main_mod, "app", failing_app)
    monkeypatch.setattr("builtins.input", lambda prompt="": waited.append(prompt) or "")
    try:
        entry.main()
    except SystemExit as e:
        assert e.code == 1
    assert waited and "Enter" in waited[0]


def test_windowed_failure_records_startup_error(project, monkeypatch):
    """창 없는 TraderApp 의 시작 실패는 state/logs/startup-error.log + OS 알림으로 남는다."""
    import pytest

    from trader.kernel.platform import notify_os

    alerts = []
    monkeypatch.setattr(notify_os, "alert", lambda t, m: alerts.append((t, m)) or True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["TraderApp"])
    monkeypatch.setattr(sys, "executable", "/x/dist/trader/TraderApp")

    def failing_app(args=None):
        raise RuntimeError("부팅 실패")

    monkeypatch.setattr(main_mod, "app", failing_app)
    with pytest.raises(SystemExit) as e:
        entry.main()
    assert e.value.code == 1
    text = (project / "state" / "logs" / "startup-error.log").read_text(encoding="utf-8")
    assert "부팅 실패" in text
    assert alerts and "부팅 실패" in alerts[0][1]


def test_translocated_path_detected():
    p = "/private/var/folders/x/T/AppTranslocation/ABC/d/Trader.app/Contents/MacOS/TraderApp"
    assert entry.translocated(p)
    assert not entry.translocated("/Users/me/trader/Trader.app/Contents/MacOS/TraderApp")


def test_ensure_streams_replaces_none(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    entry._ensure_streams()
    assert sys.stdout is not None and sys.stderr is not None
    print("ok")  # 죽지 않는다
    sys.stdout.close()
    sys.stderr.close()


def test_main_imports_native_host_only_for_chrome_origin(monkeypatch):
    """보통 CLI 인자면 세이브티커 네이티브 호스트 모듈을 import 하지 않는다(T11 — 기동 비용)."""
    calls = []
    monkeypatch.setattr(main_mod, "app", lambda args=None: calls.append(args))
    monkeypatch.setitem(sys.modules, "trader.features.saveticker.bridge_install", None)
    monkeypatch.setattr(sys, "argv", ["trader", "status"])
    entry.main()  # None 모듈을 import 하면 ImportError — 그 전에 걸러져야 한다
    assert calls == [None]
