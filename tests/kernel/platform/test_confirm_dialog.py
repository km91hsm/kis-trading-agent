from __future__ import annotations

import subprocess
from types import SimpleNamespace

from trader.kernel.platform import confirm_dialog as cd

MSG = '자동매매를 전체 일시정지할까요? 「종료」 · "따옴표" \\ 끝'


def _run(rc=None, exc=None):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        if exc:
            raise exc
        return SimpleNamespace(returncode=rc)

    return run, calls


def test_macos_return_codes_map_to_result():
    for rc, want in [(0, True), (1, False), (2, None)]:
        run, calls = _run(rc)
        assert cd.ask_yes_no("trader", MSG, platform="darwin", run=run) is want
    argv, kw = calls[0]
    assert argv[:2] == ["osascript", "-e"] and "자동매매를 전체 일시정지할까요?" in argv[2]
    assert '\\"따옴표\\"' in argv[2] and "\\\\ 끝" in argv[2]
    assert 'default button "취소"' in argv[2] and kw["timeout"] == 120


def test_macos_failures_are_none():
    for exc in (FileNotFoundError(), subprocess.TimeoutExpired("osascript", 120)):
        run, _ = _run(exc=exc)
        assert cd.ask_yes_no("t", "m", platform="darwin", run=run) is None


def test_windows_message_box_results():
    assert cd.ask_yes_no("t", "m", platform="win32", message_box=lambda *a: 6) is True
    assert cd.ask_yes_no("t", "m", platform="win32", message_box=lambda *a: 7) is False

    def boom(*a):
        raise OSError

    assert cd.ask_yes_no("t", "m", platform="win32", message_box=boom) is None


def test_other_platform_none():
    assert cd.ask_yes_no("t", "m", platform="linux") is None
