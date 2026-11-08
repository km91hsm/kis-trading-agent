from __future__ import annotations

import subprocess
from types import SimpleNamespace

from trader.kernel.platform import confirm_dialog as cd

MSG = '자동매매를 전체 일시정지할까요? 「종료」 · "따옴표" \\ 끝'


def _run(rc=None, exc=None, stderr=""):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        if exc:
            raise exc
        return SimpleNamespace(returncode=rc, stderr=stderr)

    return run, calls


CANCELED = "execution error: User canceled. (-128)"
NO_UI = "execution error: No user interaction allowed. (-1713)"


def test_macos_return_codes_map_to_result():
    for rc, err, want in [(0, "", True), (1, CANCELED, False), (1, NO_UI, None), (2, "", None)]:
        run, calls = _run(rc, stderr=err)
        assert cd.ask_yes_no("trader", MSG, platform="darwin", run=run) is want
    argv, kw = calls[0]
    assert argv[:2] == ["osascript", "-e"] and "자동매매를 전체 일시정지할까요?" in argv[2]
    assert '\\"따옴표\\"' in argv[2] and "\\\\ 끝" in argv[2]
    assert 'default button "취소"' in argv[2] and kw["timeout"] == 120
    assert kw["encoding"] == "utf-8" and kw["errors"] == "replace" and kw["capture_output"]


def test_macos_launch_failure_is_none():
    run, _ = _run(exc=FileNotFoundError())
    assert cd.ask_yes_no("t", "m", platform="darwin", run=run) is None


def test_macos_unanswered_timeout_is_false():
    """창은 떴지만 답이 없으면(시간 초과) 바꾸지 않는다 — 자리를 비운 사이 일시정지 방지."""
    run, _ = _run(exc=subprocess.TimeoutExpired("osascript", 120))
    assert cd.ask_yes_no("t", "m", platform="darwin", run=run) is False


def test_windows_message_box_results():
    assert cd.ask_yes_no("t", "m", platform="win32", message_box=lambda *a: 6) is True
    assert cd.ask_yes_no("t", "m", platform="win32", message_box=lambda *a: 7) is False

    def boom(*a):
        raise OSError

    assert cd.ask_yes_no("t", "m", platform="win32", message_box=boom) is None


def test_other_platform_none():
    assert cd.ask_yes_no("t", "m", platform="linux") is None
