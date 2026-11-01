import sqlite3
import warnings

import pytest

from trader.kernel.ledger import db
from trader.kernel.ledger.migrations import KERNEL, Migration


def _tables(c: sqlite3.Connection) -> set[str]:
    rows = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def test_connect_wal_row_factory_default_path(project):
    c = db.connect()
    try:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert c.row_factory is sqlite3.Row
        assert (project / "state" / "ledger.db").exists()
    finally:
        c.close()


def test_connect_explicit_path(tmp_path):
    c = db.connect(tmp_path / "x.db")
    c.close()
    assert (tmp_path / "x.db").exists()


def test_now_iso_utc_seconds():
    s = db.now_iso()
    assert s.endswith("+00:00") and "." not in s


def test_init_schema_idempotent(conn):
    db.init_schema(conn, KERNEL)
    assert {"schema_version", "control", "runs"} <= _tables(conn)
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    assert db.current_version(conn) == 1
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_runs_kind_started" in names


def test_applies_only_newer_in_ascending_order(conn):
    # 입력 순서와 무관하게 오름차순이어야 하므로 check 가 막는다 -> 정상 순서로 두 버전 추가
    migs = [
        *KERNEL,
        Migration(2, "f1", ("CREATE TABLE t2 (id INTEGER)",), owner="f1"),
        Migration(3, "f2", ("CREATE TABLE t3 (id INTEGER)",), owner="f2"),
    ]
    db.init_schema(conn, migs)
    assert {"t2", "t3"} <= _tables(conn)
    assert db.current_version(conn) == 3
    db.init_schema(conn, migs)
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


def test_fn_migration_applied(conn):
    def fn(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE via_fn (id INTEGER)")
        c.execute("INSERT INTO via_fn VALUES (7)")

    db.init_schema(conn, [*KERNEL, Migration(2, "fn", fn=fn, owner="f")])
    assert conn.execute("SELECT id FROM via_fn").fetchone()[0] == 7
    assert db.current_version(conn) == 2


def test_partial_failure_rolls_back(conn):
    bad = Migration(
        2, "bad", ("ALTER TABLE runs ADD COLUMN note TEXT", "CREATE TABLE nope (;"), owner="f"
    )
    with pytest.raises(sqlite3.OperationalError):
        db.init_schema(conn, [*KERNEL, bad])
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "note" not in cols
    assert db.current_version(conn) == 1


def test_fn_failure_rolls_back(conn):
    def fn(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE half (id INTEGER)")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        db.init_schema(conn, [*KERNEL, Migration(2, "fn", fn=fn, owner="f")])
    assert "half" not in _tables(conn)
    assert db.current_version(conn) == 1


def test_invalid_registry_rejected_before_any_write(conn):
    with pytest.raises(db.LedgerError):
        db.init_schema(conn, [*KERNEL, Migration(3, "gap")])
    assert db.current_version(conn) == 1


def test_latest_version_takes_no_write_lock(conn, tmp_path):
    # 다른 연결이 쓰기 잠금을 쥐고 있어도 이미 최신이면 즉시 반환해야 한다.
    other = sqlite3.connect(db_path(conn), timeout=0.1, isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        db.init_schema(conn, KERNEL)
    finally:
        other.execute("ROLLBACK")
        other.close()


def test_no_resource_warning(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        c = db.connect(tmp_path / "w.db")
        db.init_schema(c, KERNEL)
        c.close()


def db_path(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA database_list").fetchone()["file"]
