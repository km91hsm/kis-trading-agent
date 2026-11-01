import json

import pytest

from trader.kernel.ledger import control, runs


def test_control_roundtrip_json(conn):
    control.set_flag(conn, "paused", True)
    assert control.get_flag(conn, "paused") is True
    control.set_flag(conn, "cfg", {"a": [1, 2]})
    assert control.get_flag(conn, "cfg") == {"a": [1, 2]}
    assert control.get_flag(conn, "nope", 7) == 7
    assert control.get_flag(conn, "nope") is None


def test_set_flag_upsert_updates_value_and_timestamp(conn):
    control.set_flag(conn, "k", 1)
    conn.execute("UPDATE control SET updated_at='2000-01-01T00:00:00+00:00' WHERE key='k'")
    control.set_flag(conn, "k", 2)
    row = conn.execute("SELECT value, updated_at FROM control WHERE key='k'").fetchone()
    assert json.loads(row["value"]) == 2
    assert row["updated_at"] > "2000-01-01"
    assert conn.execute("SELECT COUNT(*) FROM control").fetchone()[0] == 1


def test_delete_flag(conn):
    control.set_flag(conn, "k", 1)
    control.delete_flag(conn, "k")
    assert control.get_flag(conn, "k", "gone") == "gone"
    control.delete_flag(conn, "k")  # 없는 키도 오류 없음


def test_run_lifecycle_error(conn):
    rid = runs.start(conn, "analyze", "KR")
    row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "running" and row["market"] == "KR"
    assert row["started_at"].endswith("+00:00") and row["finished_at"] is None
    runs.finish(conn, rid, "error", error="boom")
    row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    assert (row["status"], row["error"]) == ("error", "boom")
    assert row["finished_at"] and row["result_json"] is None


@pytest.mark.parametrize("status", ["ok", "skipped"])
def test_run_finish_with_result(conn, status):
    rid = runs.start(conn, "scan")
    runs.finish(conn, rid, status, result={"n": 3})
    row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["status"] == status and row["market"] is None
    assert json.loads(row["result_json"]) == {"n": 3} and row["error"] is None


def test_run_finish_rejects_unknown_status(conn):
    import sqlite3

    rid = runs.start(conn, "scan")
    with pytest.raises(sqlite3.IntegrityError):
        runs.finish(conn, rid, "weird")
