"""원장 백업: SQLite 백업 API 로 일관된 사본, 일별 14·주별(월요일) 8 보존."""

import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from trader.kernel.ledger import backup


def test_backup_copies_consistent_db(conn, project):
    conn.execute("INSERT INTO control(key, value, updated_at) VALUES ('x', '1', 'now')")
    conn.commit()
    out = backup.backup(state=project / "state", now=datetime(2026, 10, 27, 8, 10, tzinfo=UTC))
    assert out.name == "trader-20261027_081000.db" and out.parent.name == "backups"
    copy = sqlite3.connect(out)
    assert copy.execute("SELECT value FROM control WHERE key='x'").fetchone()[0] == "1"
    assert copy.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    copy.close()
    assert not list(out.parent.glob("*.tmp"))


def test_prune_keeps_recent_daily_and_monday_weekly(tmp_path):
    d0 = date(2026, 10, 27)
    for i in range(80):
        day = d0 - timedelta(days=i)
        (tmp_path / f"trader-{day:%Y%m%d}_081000.db").write_bytes(b"x")
    (tmp_path / "unrelated.txt").write_text("keep", encoding="utf-8")
    removed = backup.prune(tmp_path, today=d0, keep_daily=14, keep_weekly=8)
    kept = sorted(p.name for p in tmp_path.glob("trader-*.db"))
    days = {datetime.strptime(n[7:15], "%Y%m%d").date() for n in kept}
    recent = {d0 - timedelta(days=i) for i in range(14)}
    mondays = {d for d in days if d.weekday() == 0 and d not in recent}
    assert recent <= days and len(mondays) == 8 and days == recent | mondays
    assert removed == 80 - len(kept) and (tmp_path / "unrelated.txt").exists()


def test_backup_missing_ledger_raises_without_creating_db(tmp_path):
    """trader.db 가 없으면 빈 원장을 만들어 '성공' 백업을 남기지 않는다(설정 오류를 가리지 않게)."""
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(FileNotFoundError):
        backup.backup(state=state, now=datetime(2026, 10, 27, tzinfo=UTC))
    assert not (state / backup.DB_NAME).exists()
    assert not list(backup.backup_dir(state).glob("trader-*"))


def test_backup_failure_removes_tmp(conn, project, monkeypatch):
    """백업 API 가 실패하면 prune glob 이 못 잡는 `.db.tmp` 를 남기지 않는다."""
    real = sqlite3.connect

    class Src:
        def __init__(self, c):
            self.c = c

        def backup(self, dst):
            raise sqlite3.OperationalError("디스크 오류")

        def close(self):
            self.c.close()

    def connect(path, *a, **kw):
        c = real(path, *a, **kw)
        return Src(c) if str(path).endswith(backup.DB_NAME) else c

    monkeypatch.setattr(backup.sqlite3, "connect", connect)
    state = project / "state"
    with pytest.raises(sqlite3.OperationalError):
        backup.backup(state=state, now=datetime(2026, 10, 27, tzinfo=UTC))
    assert not list(backup.backup_dir(state).glob("*.tmp"))


def test_prune_keeps_only_last_backup_of_each_past_day(tmp_path):
    """업데이트 설치 때마다 생기는 같은 날 백업은 지난 날이면 마지막 1개만 남긴다(오늘 것은
    되돌리기용이라 전부 남긴다)."""
    today = date(2026, 11, 12)
    names = [
        "trader-20261111_081000.db", "trader-20261111_120000.db", "trader-20261111_171000.db",
        "trader-20261112_081000.db", "trader-20261112_120000.db",
    ]  # fmt: skip
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    assert backup.prune(tmp_path, today=today) == 2
    assert sorted(p.name for p in tmp_path.glob("trader-*.db")) == names[2:]
