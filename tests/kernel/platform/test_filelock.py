"""프로세스 간 파일 락 (kernel/platform/filelock)."""

import threading

import pytest

from trader.kernel.platform.filelock import LockTimeout, locked


def test_lock_is_exclusive_and_released(tmp_path):
    p = tmp_path / "x.lock"
    order: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with locked(p):
            entered.set()
            release.wait(2)
            order.append("holder-out")

    t = threading.Thread(target=holder)
    t.start()
    assert entered.wait(2)
    with pytest.raises(LockTimeout):
        with locked(p, timeout=0.2, poll_s=0.02):
            pass
    release.set()
    t.join(2)
    with locked(p, timeout=1):
        order.append("second-in")
    assert order == ["holder-out", "second-in"]


def test_try_hold_blocks_second_handle_until_released(tmp_path):
    from trader.kernel.platform.filelock import release_hold, try_hold

    p = tmp_path / "h.lock"
    first = try_hold(p)
    assert first is not None
    assert try_hold(p) is None
    release_hold(first)
    again = try_hold(p)
    assert again is not None
    release_hold(again)
    release_hold(None)
