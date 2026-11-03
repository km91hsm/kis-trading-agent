import sqlite3

import pytest

from trader.kernel.execution.migrations import EXECUTION
from trader.kernel.registry import contracts, discover
from trader.kernel.registry.contracts import all_migrations


def test_all_migrations_include_execution_v5(conn):
    migs = all_migrations(discover())
    assert [m.version for m in migs] == list(range(1, len(migs) + 1))
    assert next(m for m in migs if m.version == 5) is EXECUTION
    assert EXECUTION.owner == "kernel/execution"
    assert EXECUTION in contracts.KERNEL_MODULES
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "orders", "approvals", "risk_checks", "exec_locks", "buy_reservations",
        "positions_snapshot", "account_nav", "fills",
    } <= tables  # fmt: skip
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_fills_order", "ix_orders_account_created", "ix_orders_decision"} <= indexes


def test_schema_constraints(conn):
    def order(**kw):
        row = {
            "account_id": "a", "ticker": "005930", "market": "KR", "side": "BUY", "qty": 1,
            "order_type": "LIMIT", "status": "accepted", "mode": "auto", "created_at": "t",
        }  # fmt: skip
        row.update(kw)
        cols = ",".join(row)
        return conn.execute(
            f"INSERT INTO orders({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values())
        )

    order()  # origin·asset 기본값
    r = conn.execute("SELECT origin, asset FROM orders").fetchone()
    assert (r["origin"], r["asset"]) == ("llm", "stock")
    for bad in ({"side": "HOLD"}, {"status": "weird"}, {"origin": "x"}, {"asset": "etf"}):
        with pytest.raises(sqlite3.IntegrityError):
            order(**bad)
    conn.execute("INSERT INTO exec_locks(key, created_at) VALUES ('k', 't')")
    conn.execute(
        "INSERT INTO buy_reservations(key, account_id, market, ticker, side, amount) "
        "VALUES ('k','a','KR','005930','BUY','1')"
    )
    conn.execute("DELETE FROM exec_locks WHERE key='k'")
    assert conn.execute("SELECT COUNT(*) FROM buy_reservations").fetchone()[0] == 0  # CASCADE
