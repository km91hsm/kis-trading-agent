import logging
import os
import sys
import time

import pytest

from trader.kernel.platform import logging_setup


@pytest.fixture(autouse=True)
def _reset_logging():
    yield
    logging_setup.reset()


def _flush():
    for h in logging.getLogger().handlers:
        h.flush()


def test_configure_creates_log_and_quiets_httpx(project):
    logging_setup.configure(project / "state")
    logging.getLogger("trader.test").info("안녕 로그")
    _flush()
    log = project / "state" / "logs" / "trader.log"
    assert log.exists() and "안녕 로그" in log.read_text(encoding="utf-8")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_configure_twice_no_duplicate_handlers(project):
    logging_setup.configure(project / "state")
    logging_setup.configure(project / "state")
    ours = [h for h in logging.getLogger().handlers if getattr(h, "name", "") == "trader"]
    assert len(ours) == 2  # 파일 + 콘솔


def test_reset_removes_handlers(project):
    logging_setup.configure(project / "state")
    logging_setup.reset()
    assert not [h for h in logging.getLogger().handlers if getattr(h, "name", "") == "trader"]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows 는 열린 파일을 rename 못 해 다른 프로세스가 먼저 회전할 수 없다",
)
def test_rotation_by_another_process_reopens_stream(project):
    """한쪽이 자정 회전을 끝내면 다른 쪽은 보관 파일이 아니라 새 trader.log 를 다시 열어야 한다."""
    log_dir = project / "state" / "logs"
    logging_setup.configure(project / "state")
    mine = next(h for h in logging.getLogger().handlers if hasattr(h, "rolloverAt"))
    other = logging_setup.file_handler(log_dir / "trader.log")  # 다른 프로세스 흉내
    for h in (mine, other):
        h.rolloverAt = int(time.time()) - 1
    rec = logging.LogRecord("trader.other", logging.INFO, __file__, 1, "다른 프로세스", None, None)
    other.emit(rec)
    other.close()
    archives = list(log_dir.glob("trader.log.*"))
    assert len(archives) == 1
    logging.getLogger("trader.test").info("회전 뒤 기록")
    mine.flush()
    assert "회전 뒤 기록" in (log_dir / "trader.log").read_text(encoding="utf-8")
    assert "회전 뒤 기록" not in archives[0].read_text(encoding="utf-8")


def test_configure_with_name_uses_separate_file(project):
    """name 을 주면 `<name>.log` 에 쓴다. 기본은 trader.log."""
    logging_setup.configure(project / "state", name="web")
    logging.getLogger("trader.test").info("웹 로그")
    _flush()
    assert "웹 로그" in (project / "state" / "logs" / "web.log").read_text(encoding="utf-8")
    assert not (project / "state" / "logs" / "trader.log").exists()


def test_rotation_permission_error_keeps_writing(tmp_path, monkeypatch):
    """doRollover 중 os.rename/os.replace 가 PermissionError(Windows 파일 점유)여도 삼키고,
    이후 emit 이 같은 파일에 예외 없이 이어 쓴다."""
    log = tmp_path / "t.log"
    handler = logging_setup.file_handler(log)
    handler.setFormatter(logging.Formatter("%(message)s"))
    errors: list = []
    monkeypatch.setattr(handler, "handleError", lambda rec: errors.append(rec))

    def locked(*a, **k):
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(os, "rename", locked)  # 파이썬 버전에 따라 rename/replace 중 하나를 쓴다
    monkeypatch.setattr(os, "replace", locked)
    try:
        handler.rolloverAt = int(time.time()) - 1
        handler.doRollover()  # 예외 없이 끝난다
        assert handler.rolloverAt > int(time.time())
        rec = logging.LogRecord("trader.t", logging.INFO, __file__, 1, "이어 쓰기", None, None)
        handler.emit(rec)
        handler.flush()
    finally:
        handler.close()
    assert not errors
    assert "이어 쓰기" in log.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("t.log.*"))


def test_configure_without_stderr_skips_stream_handler(project, monkeypatch):
    """창 없는 실행파일은 stderr 가 None — 파일 핸들러만 붙이고 죽지 않는다."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    logging_setup.configure(project / "state")
    mine = [h for h in logging.getLogger().handlers if getattr(h, "name", None) == "trader"]
    assert len(mine) == 1 and not any(type(h) is logging.StreamHandler for h in mine)
