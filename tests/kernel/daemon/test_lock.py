"""데몬 잠금: 원자 생성·stale 인수·살아 있는 보유자 거부·OS 잠금 수명."""

import json
import os
from pathlib import Path

from trader.kernel.daemon.lock import Lock

DEAD_PID = 2**31 - 5  # 존재할 수 없는 pid


def _write(path: Path, pid: int, start: str | None = None) -> None:
    path.write_text(json.dumps({"pid": pid, "proc_start": start}), encoding="utf-8")


def test_acquire_writes_pid_and_release_removes(tmp_path):
    p = tmp_path / "daemon.lock"
    lock = Lock(p)
    assert lock.acquire() is True
    assert json.loads(p.read_text(encoding="utf-8"))["pid"] == os.getpid()
    assert lock.held() is True
    lock.release()
    assert not p.exists()
    assert lock.held() is False


def test_second_acquire_on_same_instance_refused(tmp_path):
    lock = Lock(tmp_path / "daemon.lock")
    assert lock.acquire() is True
    assert lock.acquire() is False
    assert lock.held() is True  # 첫 보유는 그대로
    lock.release()


def test_second_lock_object_refused_while_held(tmp_path):
    p = tmp_path / "daemon.lock"
    first = Lock(p)
    assert first.acquire() is True
    assert Lock(p).acquire() is False
    first.release()
    other = Lock(p)
    assert other.acquire() is True
    other.release()


def test_stale_lock_from_dead_pid_is_taken_over(tmp_path):
    p = tmp_path / "daemon.lock"
    _write(p, DEAD_PID)
    assert Lock(p).held() is False
    lock = Lock(p)
    assert lock.acquire() is True
    assert json.loads(p.read_text(encoding="utf-8"))["pid"] == os.getpid()
    lock.release()


def test_live_pid_with_matching_start_blocks_pidfile(tmp_path):
    """OS 잠금이 풀려 있어도 pid 파일의 보유자가 살아 있으면 거부한다."""
    from trader.kernel.platform.procs import process_start

    p = tmp_path / "daemon.lock"
    _write(p, os.getpid(), process_start(os.getpid()))
    lock = Lock(p)
    assert lock.held() is True
    assert lock.acquire() is False
    assert lock._hold is None  # 실패하면 OS 잠금도 놓는다


def test_live_pid_with_different_start_is_stale(tmp_path):
    """pid 가 재사용된 경우(시작 시각 불일치)는 죽은 잠금이다."""
    from trader.kernel.platform.procs import process_start

    if process_start(os.getpid()) is None:  # pragma: no cover - 식별자를 못 구하는 OS
        return
    p = tmp_path / "daemon.lock"
    _write(p, os.getpid(), "utc:Thu Jan  1 00:00:00 1970")
    lock = Lock(p)
    assert lock.held() is False
    assert lock.acquire() is True
    lock.release()


def test_fresh_empty_lock_is_treated_as_being_created(tmp_path):
    p = tmp_path / "daemon.lock"
    p.write_text("", encoding="utf-8")
    assert Lock(p).acquire() is False


def test_old_empty_lock_is_taken_over(tmp_path):
    p = tmp_path / "daemon.lock"
    p.write_text("", encoding="utf-8")
    old = p.stat().st_mtime - 3600
    os.utime(p, (old, old))
    lock = Lock(p)
    assert lock.acquire() is True
    lock.release()


def test_release_keeps_lock_file_of_other_pid(tmp_path):
    p = tmp_path / "daemon.lock"
    lock = Lock(p)
    assert lock.acquire() is True
    _write(p, DEAD_PID)
    lock.release()
    assert p.exists()


def test_link_unsupported_falls_back_to_o_excl(tmp_path, monkeypatch):
    def no_link(src, dst):
        raise OSError("hardlink 미지원")

    monkeypatch.setattr(os, "link", no_link)
    p = tmp_path / "daemon.lock"
    lock = Lock(p)
    assert lock.acquire() is True
    assert json.loads(p.read_text(encoding="utf-8"))["pid"] == os.getpid()
    lock.release()
