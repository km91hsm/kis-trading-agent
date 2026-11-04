import json

import pytest

from tests.features.accounts.conftest import make_account
from trader.features.accounts import modes
from trader.features.scan import watchlist
from trader.kernel.ledger import control, db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    yield c
    c.close()


def test_log_change_serializes_and_orders(conn):
    a = control.log_change(
        conn, channel="web", actor="t1", key="k", old=None, new={"가": [1]},
        at="2026-01-01T00:00:00+00:00",
    )
    b = control.log_change(
        conn, channel="cli", actor=None, key="k", old={"가": [1]}, new=False,
        at="2026-01-01T00:00:01+00:00",
    )
    assert b > a
    rows = conn.execute("SELECT * FROM control_log ORDER BY id").fetchall()
    assert rows[0]["old"] is None and json.loads(rows[0]["new"]) == {"가": [1]}
    assert "가" in rows[0]["new"]  # ensure_ascii=False
    assert rows[1]["actor"] is None and json.loads(rows[1]["old"]) == {"가": [1]}
    assert rows[1]["new"] == "false"


def test_set_paused_and_mode_write_control_log(conn):
    acc = make_account("paper-main", "paper", "auto")
    modes.set_paused(conn, True, channel="web", actor="t123")
    modes.set_account_mode(conn, acc, "off", channel="web", actor="t123")
    watchlist.set_watchlist(conn, ["005930"], channel="web", actor="t123")
    rows = conn.execute("SELECT * FROM control_log ORDER BY id").fetchall()
    assert [(r["key"], r["channel"], r["actor"]) for r in rows] == [
        ("paused", "web", "t123"),
        ("account_mode:paper-main", "web", "t123"),
        ("watchlist", "web", "t123"),
    ]
    assert (rows[0]["old"], rows[0]["new"]) == ("false", "true")
    assert (json.loads(rows[1]["old"]), json.loads(rows[1]["new"])) == ("auto", "off")
    assert (json.loads(rows[2]["old"]), json.loads(rows[2]["new"])) == ([], ["005930"])


def test_defaults_log_as_cli(conn):
    modes.set_paused(conn, True)
    row = conn.execute("SELECT channel, actor FROM control_log").fetchone()
    assert (row["channel"], row["actor"]) == ("cli", None)


def test_same_value_writes_no_control_log_row(conn):
    acc = make_account("paper-main", "paper", "auto")
    modes.set_paused(conn, True)
    modes.set_account_mode(conn, acc, "off")
    watchlist.set_watchlist(conn, ["005930"])
    n = conn.execute("SELECT COUNT(*) FROM control_log").fetchone()[0]
    modes.set_paused(conn, True)
    modes.set_account_mode(conn, acc, "off")
    watchlist.set_watchlist(conn, ["005930"])
    assert conn.execute("SELECT COUNT(*) FROM control_log").fetchone()[0] == n == 3
