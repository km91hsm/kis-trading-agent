"""execution 원장 v7 원시 함수: 체결 적재·주문 체결 갱신·승인 만료·열린 지정가 조회."""

from datetime import date
from decimal import Decimal

from trader.kernel.execution import ledger as L

ACCT = "paper-main"
D = date(2026, 11, 2)


def _order(conn, *, side="BUY", status="accepted", origin=None, created_at, ticker="005930"):
    return L.insert_order(
        conn,
        decision_id=None,
        account_id=ACCT,
        ticker=ticker,
        market="KR",
        side=side,
        qty=2,
        order_type="LIMIT",
        price=Decimal("100"),
        status=status,
        order_no="1",
        message="",
        mode="auto",
        created_at=created_at,
        origin=origin,
    )


def test_insert_fill_dedupes_same_fill(conn):
    oid = _order(conn, created_at="2026-11-02T01:00:00+00:00")
    kw = dict(
        order_id=oid,
        account_id=ACCT,
        market="KR",
        ticker="005930",
        side="BUY",
        qty=2,
        price=Decimal("100"),
        filled_at="2026-11-02T01:05:00+00:00",
        order_no="1",
    )
    assert isinstance(L.insert_fill(conn, **kw), int)
    assert L.insert_fill(conn, **kw) is None  # 같은 체결은 무시
    row = conn.execute("SELECT order_no, ticker, side, market FROM fills").fetchone()
    assert tuple(row) == ("1", "005930", "BUY", "KR")
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1


def test_update_order_fill_marks_partial_then_filled(conn):
    oid = _order(conn, created_at="2026-11-02T01:00:00+00:00")
    L.update_order_fill(conn, oid, filled_qty=1, status="partial")
    row = conn.execute("SELECT status, filled_qty FROM orders WHERE id=?", (oid,)).fetchone()
    assert (row["status"], row["filled_qty"]) == ("partial", 1)
    L.update_order_fill(conn, oid, filled_qty=2, status="filled")
    row = conn.execute("SELECT status, filled_qty FROM orders WHERE id=?", (oid,)).fetchone()
    assert (row["status"], row["filled_qty"]) == ("filled", 2)


def _approval(conn, side, ticker="005930", account=ACCT):
    return L.create_approval(
        conn,
        decision_id=1,
        account_id=account,
        order={"ticker": ticker, "market": "KR", "side": side, "qty": 1},
        requested_at="2026-11-02T01:00:00+00:00",
        expires_at="2026-11-02T01:30:00+00:00",
        fingerprint="fp" + side + ticker + account,
    )


def test_expire_pending_buys_expires_only_pending_buy_approvals(conn):
    buy = _approval(conn, "BUY")
    sell = _approval(conn, "SELL")
    other_ticker = _approval(conn, "BUY", ticker="000660")
    other_acct = _approval(conn, "BUY", account="paper-b")
    decided = _approval(conn, "BUY")
    L.decide_approval(conn, decided, "rejected", decided_at="2026-11-02T01:01:00+00:00")
    ids = L.expire_pending_buys(
        conn, account_id=ACCT, market="KR", ticker="005930", now="2026-11-02T02:00:00+00:00"
    )
    assert ids == [buy]
    status = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM approvals")}
    assert status[buy] == "expired"
    assert status[sell] == status[other_ticker] == status[other_acct] == "pending"
    assert status[decided] == "rejected"


def test_open_limit_orders_today_excludes_dead(conn):
    live = _order(conn, created_at="2026-11-02T01:00:00+00:00")
    _order(conn, status="cancelled", created_at="2026-11-02T01:00:00+00:00")
    _order(conn, status="rejected", created_at="2026-11-02T01:00:00+00:00")
    _order(conn, origin="stop", created_at="2026-11-02T01:00:00+00:00")
    _order(conn, origin="hedge", created_at="2026-11-02T01:00:00+00:00")
    _order(conn, created_at="2026-11-01T01:00:00+00:00")  # 어제
    reprice = _order(conn, origin="reprice", created_at="2026-11-02T02:00:00+00:00")
    rows = L.open_limit_orders_today(conn, ACCT, "KR", D)
    assert [r["id"] for r in rows] == [live, reprice]
    assert all(isinstance(r, dict) for r in rows)
    rows = L.open_limit_orders_today(conn, ACCT, "KR", D, exclude_origins=())
    assert len(rows) == 4
