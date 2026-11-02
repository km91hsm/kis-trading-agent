import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from trader.kernel.broker.models import Position
from trader.kernel.execution import ledger

D = date(2026, 11, 2)  # 월요일(KR 은 KST, US 는 EST — 서머타임 종료 뒤)
NOON = "2026-11-02T03:00:00+00:00"  # KST 12:00, ET 새벽 — 둘 다 D 안이 아니라 KR 만 D 안
KR_DAY = "2026-11-02T03:00:00+00:00"


def _order(
    conn,
    *,
    ticker="005930",
    side="BUY",
    qty=1,
    price="100",
    status="accepted",
    market="KR",
    created_at=KR_DAY,
    origin=None,
    account="a",
    amount=None,
    decision=None,
):
    return ledger.insert_order(
        conn, decision_id=decision, account_id=account, ticker=ticker, market=market, side=side,
        qty=qty, order_type="LIMIT", price=Decimal(price), status=status, order_no="1",
        message="", mode="auto", created_at=created_at, origin=origin,
        amount=Decimal(amount) if amount else None,
    )  # fmt: skip


def _fill(conn, oid, qty, price, at, account="a"):
    conn.execute(
        "INSERT INTO fills(order_id, account_id, qty, price, filled_at) VALUES (?,?,?,?,?)",
        (oid, account, qty, price, at),
    )
    conn.commit()


def test_insert_order_defaults_and_check_violations(conn):
    oid = _order(conn, qty=3, price="70000")
    r = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    assert (r["amount"], r["origin"], r["asset"], r["price"]) == ("210000", "llm", "stock", "70000")
    ledger.set_order_status(conn, oid, "filled")
    assert conn.execute("SELECT status FROM orders").fetchone()[0] == "filled"
    with pytest.raises(sqlite3.IntegrityError):
        ledger.set_order_status(conn, oid, "bogus")
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        _order(conn, status="bogus")
    with pytest.raises(ValueError, match="origin"):
        _order(conn, origin="nope")
    assert _order(conn, origin="stop", amount="5")  # 명시 금액 우선
    assert conn.execute("SELECT amount FROM orders ORDER BY id DESC").fetchone()[0] == "5"


def test_has_open_order_or_approval(conn):
    assert not ledger.has_open_order_or_approval(conn, 7, "a")
    oid = _order(conn, decision=7, status="filled")
    assert ledger.has_open_order_or_approval(conn, 7, "a")
    assert not ledger.has_open_order_or_approval(conn, 7, "a", include_filled=False)
    assert not ledger.has_open_order_or_approval(conn, 7, "a", "SELL")
    assert ledger.has_open_order_or_approval(conn, 7, "a", "BUY")
    ledger.set_order_status(conn, oid, "cancelled")
    assert not ledger.has_open_order_or_approval(conn, 7, "a")
    aid = ledger.create_approval(
        conn, decision_id=7, account_id="a", order={"side": "BUY", "ticker": "x"},
        requested_at=NOON, expires_at="2026-11-02T04:00:00+00:00", fingerprint="fp",
    )  # fmt: skip
    assert ledger.has_open_order_or_approval(conn, 7, "a")
    assert ledger.has_open_order_or_approval(conn, 7, "a", "BUY", now=NOON)
    assert not ledger.has_open_order_or_approval(conn, 7, "a", "SELL")
    assert not ledger.has_open_order_or_approval(conn, 7, "a", now="2026-11-02T05:00:00+00:00")
    ledger.decide_approval(conn, aid, "rejected")
    assert not ledger.has_open_order_or_approval(conn, 7, "a")


def test_create_decide_approval_race_and_side_channels(conn):
    aid = ledger.create_approval(
        conn, decision_id=3, account_id="a",
        order={"ticker": "005930", "side": "BUY", "qty": 2, "price": None, "market": "KR"},
        requested_at=NOON, expires_at="2026-11-02T04:00:00+00:00", fingerprint="fp",
        stage="50_order", exec_key="k1", origin="stop", quote_price=Decimal("70000"),
    )  # fmt: skip
    col = conn.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
    assert (col["stage"], col["exec_key"], col["origin"], col["quote_price"]) == (
        "50_order", "k1", "stop", "70000",
    )  # fmt: skip
    assert "exec_key" not in col["order_json"] and "quote_price" not in col["order_json"]
    a = ledger.get_approval(conn, aid)
    assert a["status"] == "pending" and a["fingerprint"] == "fp"
    assert a["order"]["order_stage"] == "50_order" and a["order"]["exec_key"] == "k1"
    assert a["order"]["origin"] == "stop" and a["order"]["quote_price"] == "70000"
    assert [x["id"] for x in ledger.pending_approvals(conn)] == [aid]
    assert ledger.get_approval(conn, 999) is None
    # 조건부 UPDATE: 먼저 처리한 쪽만 True
    assert ledger.decide_approval(conn, aid, "approved", decided_at=NOON)
    assert not ledger.decide_approval(conn, aid, "rejected")
    assert ledger.decide_approval(conn, aid, "failed", expect="approved")
    assert ledger.get_approval(conn, aid)["status"] == "failed"
    assert ledger.pending_approvals(conn) == []


def test_expire_pending(conn):
    def mk(exp):
        return ledger.create_approval(
            conn, decision_id=1, account_id="a", order={"side": "BUY"}, requested_at=NOON,
            expires_at=exp, fingerprint="f",
        )  # fmt: skip

    old, live = mk("2026-11-02T03:30:00+00:00"), mk("2026-11-02T05:00:00+00:00")
    assert ledger.expire_pending(conn, "2026-11-02T04:00:00+00:00") == [old]
    assert ledger.get_approval(conn, old)["status"] == "expired"
    assert ledger.get_approval(conn, old)["decided_at"] == "2026-11-02T04:00:00+00:00"
    assert ledger.get_approval(conn, live)["status"] == "pending"
    assert ledger.expire_pending(conn, "2026-11-02T04:00:00+00:00") == []


def test_insert_risk_check(conn):
    rid = ledger.insert_risk_check(
        conn,
        decision_id=3,
        account_id="a",
        approved=False,
        adjusted_qty=None,
        reasons=["한도 초과"],
    )
    r = conn.execute("SELECT * FROM risk_checks WHERE id=?", (rid,)).fetchone()
    assert (r["approved"], r["adjusted_qty"]) == (0, None)
    assert r["reasons_json"] == '["한도 초과"]' and r["created_at"]


def test_snapshot_positions_and_account_nav(conn):
    def pos(t, q=1):
        return Position(
            ticker=t, market="KR", qty=q, avg_price=Decimal("100"), current_price=Decimal("110"),
            eval_amount=Decimal(110 * q), pnl_pct=10.0,
        )  # fmt: skip

    ledger.snapshot_positions(
        conn, account_id="a", d=D, nav=Decimal("1000"), cash=Decimal("300"),
        positions=[pos("A"), pos("B", 2)], market="KR",
    )  # fmt: skip
    ledger.snapshot_positions(  # 같은 날 다시: B 전량 매도 → 행 삭제, A 갱신, NAV upsert
        conn, account_id="a", d=D, nav=Decimal("1100"), cash=Decimal("400"),
        positions=[{"ticker": "A", "qty": 5, "avg_price": "100"}], market="KR",
    )  # fmt: skip
    rows = conn.execute("SELECT ticker, qty, avg_price FROM positions_snapshot").fetchall()
    assert [(r["ticker"], r["qty"], r["avg_price"]) for r in rows] == [("A", 5, "100")]
    navs = conn.execute("SELECT nav, cash FROM account_nav").fetchall()
    assert [(r["nav"], r["cash"]) for r in navs] == [("1100", "400")]
    # 포지션이 하나도 없어도 NAV 는 남는다. 다른 시장 행은 건드리지 않는다
    ledger.snapshot_positions(
        conn, account_id="a", d=D, nav=Decimal("9"), cash=Decimal("9"), positions=[], market="US"
    )  # fmt: skip
    assert conn.execute("SELECT COUNT(*) FROM positions_snapshot").fetchone()[0] == 1
    ledger.snapshot_positions(
        conn, account_id="a", d=date(2026, 11, 3), nav=Decimal("1200"), cash=Decimal("0"),
        positions=[], market="KR",
    )  # fmt: skip
    assert ledger.latest_nav_before(conn, "a", date(2026, 11, 3), "KR") == Decimal("1100")
    assert ledger.latest_nav_before(conn, "a", D, "KR") is None
    assert ledger.nav_history(conn, "a", "KR", days=30) == [
        ("2026-11-02", Decimal("1100")), ("2026-11-03", Decimal("1200")),
    ]  # fmt: skip
    # 주말 표본은 뺀다(11-07 토)
    ledger.snapshot_positions(
        conn, account_id="a", d=date(2026, 11, 7), nav=Decimal("1"), cash=Decimal("0"),
        positions=[], market="KR",
    )  # fmt: skip
    assert [d for d, _ in ledger.nav_history(conn, "a", "KR", days=30)] == [
        "2026-11-02", "2026-11-03",
    ]  # fmt: skip


def test_today_order_amount_and_pending_buy_amounts(conn):
    _order(conn, ticker="A", qty=2, price="100")  # 200
    _order(conn, ticker="A", qty=1, price="1", status="filled", amount="1000")
    _order(conn, ticker="B", status="cancelled", qty=9, price="9")
    _order(conn, ticker="C", side="SELL", qty=9, price="9")
    _order(conn, ticker="U", market="US", qty=1, price="5", created_at="2026-11-02T15:00:00+00:00")
    ledger.create_approval(  # 승인 대기: 가격 없으면 quote_price 열
        conn, decision_id=1, account_id="a",
        order={"ticker": "B", "side": "BUY", "qty": 3, "price": None, "market": "KR"},
        requested_at=NOON, expires_at="2026-11-02T04:00:00+00:00", fingerprint="f",
        quote_price=Decimal("50"),
    )  # fmt: skip
    assert ledger.today_order_amount(conn, "a", D, "KR") == Decimal("1350")
    assert ledger.today_order_amount(conn, "a", D, "KR", exclude_filled=True) == Decimal("350")
    later = "2026-11-02T05:00:00+00:00"  # 승인 만료 뒤
    assert ledger.today_order_amount(conn, "a", D, "KR", exclude_filled=True, now=later) == Decimal(
        "200"
    )
    assert ledger.pending_buy_amounts(conn, "a", D, "KR") == {
        "A": Decimal("200"),
        "B": Decimal("150"),
    }
    assert ledger.today_buy_tickers(conn, "a", D, "KR") == {"A"}
    assert ledger.today_order_amount(conn, "a", D, "US") == Decimal("5")


def test_held_since(conn):
    a1 = _order(conn, ticker="A", qty=10)
    a2 = _order(conn, ticker="A", side="SELL", qty=10)
    a3 = _order(conn, ticker="A", qty=5)
    _fill(conn, a1, 10, "100", "2026-10-26T01:00:00+00:00")
    _fill(conn, a2, 10, "100", "2026-10-28T01:00:00+00:00")
    _fill(conn, a3, 5, "100", "2026-10-30T01:00:00+00:00")
    _fill(conn, _order(conn, ticker="Z", qty=4), 4, "1", "2026-10-20T01:00:00+00:00")
    _fill(conn, _order(conn, ticker="Z", side="SELL", qty=4), 4, "1", "2026-10-21T01:00:00+00:00")
    # 체결 기록이 없는 종목은 접수된 주문으로(죽은 주문 제외)
    _order(conn, ticker="B", created_at="2026-10-27T01:00:00+00:00")
    _order(conn, ticker="C", created_at="2026-10-27T01:00:00+00:00", status="cancelled")
    # US 는 현지(ET) 날짜: UTC 10-27 02:00 = ET 10-26 22:00
    _order(conn, ticker="U", market="US", created_at="2026-10-27T02:00:00+00:00")
    assert ledger.held_since(conn, "a") == {
        "A": date(2026, 10, 30), "B": date(2026, 10, 27), "U": date(2026, 10, 26),
    }  # fmt: skip
    assert ledger.held_since(conn, "a", ["B"]) == {"B": date(2026, 10, 27)}
    assert ledger.held_since(conn, "a", []) == {}


def test_realized_pnl_5d(conn):
    b = _order(conn, ticker="A", qty=10)
    s = _order(conn, ticker="A", side="SELL", qty=10)
    sc = _order(conn, ticker="A", side="SELL", qty=1, origin="scalp")
    _fill(conn, b, 10, "100", "2026-10-30T01:00:00+00:00")
    _fill(conn, s, 4, "120", "2026-11-02T01:00:00+00:00")  # (120-100)*4 = 80
    _fill(conn, sc, 1, "50", "2026-11-02T02:00:00+00:00")  # 단타 매도: 보유만 줄이고 손익 제외
    assert ledger.realized_pnl_5d(conn, "a", D, "KR") == Decimal("80")
    assert ledger.realized_pnl_5d(conn, "a", D, "US") == Decimal("0")


def test_trades_this_week_and_open_orders(conn):
    wed = date(2026, 11, 4)
    _order(conn, ticker="A", created_at="2026-11-02T01:00:00+00:00")
    _order(conn, ticker="A", created_at="2026-11-03T01:00:00+00:00", origin="stop")
    _order(conn, ticker="A", created_at="2026-11-03T02:00:00+00:00", origin="scalp")
    _order(conn, ticker="A", created_at="2026-11-03T03:00:00+00:00", status="rejected")
    _order(conn, ticker="A", created_at="2026-10-30T01:00:00+00:00")  # 지난주
    _order(conn, ticker="A", market="US", created_at="2026-11-03T04:00:00+00:00")
    _order(conn, ticker="B", created_at="2026-11-04T01:00:00+00:00")
    assert ledger.trades_this_week(conn, "a", wed, "KR") == {"A": 2, "B": 1}
    assert ledger.trades_this_week(conn, "a", wed, "US") == {"A": 1}
    assert ledger.trades_this_week(conn, "a", wed) == {"A": 3, "B": 1}
    oid = _order(conn, ticker="O", created_at=KR_DAY)
    _order(conn, ticker="O", created_at=KR_DAY, status="filled")
    rows = ledger.open_orders_today(conn, "a", "O", "BUY", D, market="KR")
    assert [r["id"] for r in rows] == [oid]
    assert ledger.open_orders_today(conn, "a", "O", "SELL", D) == []


def test_day_bounds_and_marks(conn):
    assert ledger.day_bounds(D, "KR") == ("2026-11-01T15:00:00+00:00", "2026-11-02T15:00:00+00:00")
    assert ledger.day_bounds(D, "US") == ("2026-11-02T05:00:00+00:00", "2026-11-03T05:00:00+00:00")
    assert ledger.day_bounds(D, None) == ledger.day_bounds(D, "KR")
    assert ledger.day_bounds(D, "XX") == ledger.day_bounds(D, "KR")
    m0 = ledger.ledger_marks(conn)
    assert (m0["order_id"], m0["approval_id"]) == (0, 0) and m0["at"]
    oid = _order(conn)
    assert ledger.ledger_marks(conn)["order_id"] == oid
