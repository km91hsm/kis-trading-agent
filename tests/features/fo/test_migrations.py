"""마이그레이션 14 fo: 5 테이블·인덱스·UNIQUE·request_id 열."""

import sqlite3

import pytest

from trader.features.fo.migrations import MIGRATIONS
from trader.kernel.ledger import db

TABLES = (
    "fo_positions", "fo_order_legs", "fo_decisions", "fo_nav_snapshot", "fo_positions_snapshot"
)  # fmt: skip


def _names(conn, kind):
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))}


def test_single_migration_14_owned_by_fo():
    assert [(m.version, m.name, m.owner) for m in MIGRATIONS] == [(14, "fo", "fo")]


def test_tables_and_indexes(conn):
    assert set(TABLES) <= _names(conn, "table")
    idx = _names(conn, "index")
    assert {
        "ix_fo_positions_account_status", "ix_fo_legs_account_status", "ix_fo_legs_request",
        "ux_fo_decisions_key",
    } <= idx  # fmt: skip
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(fo_order_legs)")}
    assert "request_id" in cols


def test_current_version_is_20(conn):
    assert db.current_version(conn) == 20


def _leg(conn, **kw):
    row = {"position_id": 1, "account_id": "a", "intent": "OPEN", "round": 0, "attempt": 1,
           "status": "prepared", "qty": 1, "created_at": "t"} | kw  # fmt: skip
    cols = ", ".join(row)
    conn.execute(
        f"INSERT INTO fo_order_legs({cols}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )


def test_leg_unique_key(conn):
    _leg(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _leg(conn)
    _leg(conn, attempt=2)


def _decision(conn, **kw):
    row = {"date": "2026-10-27", "account_id": None, "position_id": None, "round": 0,
           "request_id": "r", "intent": "OPEN", "direction": "LONG", "vehicle": "FUTURES",
           "data": "{}", "created_at": "t"} | kw  # fmt: skip
    cols = ", ".join(row)
    conn.execute(
        f"INSERT INTO fo_decisions({cols}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )


def test_morning_decision_unique_despite_null_keys(conn):
    _decision(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _decision(conn, request_id="r2")
    _decision(conn, round=1)
    _decision(conn, account_id="a", position_id=3)
