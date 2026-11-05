"""OS 알림: 실제 osascript·MessageBox 는 띄우지 않고 호출 인자만 본다."""

import subprocess
import types

from trader.kernel.platform import notify_os


def test_alert_darwin_calls_osascript_with_quoted_text(monkeypatch):
    calls = []
    monkeypatch.setattr(notify_os.sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    assert notify_os.alert('제목 "a"', "본문\\b") is True
    (argv, kw), = calls
    assert argv[:2] == ["/usr/bin/osascript", "-e"]
    assert argv[2] == 'display notification "본문\\\\b" with title "제목 \\"a\\""'
    assert kw["shell"] is False and kw["encoding"] == "utf-8" and kw["errors"] == "replace"


def test_alert_win32_uses_messagebox_and_failure_is_false(monkeypatch):
    import ctypes

    boxes = []
    user32 = types.SimpleNamespace(MessageBoxW=lambda *a: boxes.append(a))
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=user32), raising=False)
    monkeypatch.setattr(notify_os.sys, "platform", "win32")
    assert notify_os.alert("제목", "본문") is True
    assert boxes == [(None, "본문", "제목", 0x10)]
    monkeypatch.setattr(notify_os.sys, "platform", "darwin")

    def boom(*a, **k):
        raise OSError("no osascript")

    monkeypatch.setattr(subprocess, "run", boom)
    assert notify_os.alert("t", "m") is False
    monkeypatch.setattr(notify_os.sys, "platform", "linux")
    assert notify_os.alert("t", "m") is False
