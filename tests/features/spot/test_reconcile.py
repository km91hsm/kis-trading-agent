"""야간 대사: 브로커 보유 vs 원장 스냅샷, 마감 뒤 미정리 주문. 브로커가 진실."""

import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from tests.features.spot.approval_helpers import make_account, make_port
from trader.features.accounts.settings import to_broker_account
from trader.features.spot import jobs as spot_jobs
from trader.features.spot import reconcile
from trader.features.spot.ledger import latest_positions
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash, Position
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import control, db

D = date(2026, 10, 24)
T0 = datetime(2026, 10, 24, 7, 40, tzinfo=UTC)


class Notes:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text, **_):
        self.sent.append(text)
        return True


def _pos(ticker, qty):
    return Position(
        ticker=ticker, market="KR", qty=qty, avg_price=Decimal("1000"),
        current_price=Decimal("1100"), eval_amount=Decimal(1100 * qty), pnl_pct=0.1,
    )  # fmt: skip


def _broker(acct, positions):
    cash = {
        "KR": Cash(
            currency="KRW", available=Decimal("1000000"), total=Decimal("1000000"),
            nav=Decimal("2000000"),
        )
    }  # fmt: skip
    return FakeBroker(to_broker_account(acct), cash=cash, positions=positions)


def _snap(conn, qty=10):
    xledger.snapshot_positions(
        conn, account_id="paper-main", d=D, market="KR", nav=Decimal(1), cash=Decimal(1),
        positions=[{"ticker": "005930", "market": "KR", "qty": qty}],
    )  # fmt: skip


def _order(conn):
    cur = conn.execute(
        "INSERT INTO orders(decision_id, account_id, ticker, market, side, qty, order_type, "
        "order_no, status, message, mode, created_at) "
        "VALUES (NULL,'paper-main','005930','KR','BUY',10,'MARKET','X1','accepted','','auto',?)",
        (T0.isoformat(),),
    )
    conn.commit()
    return cur.lastrowid


def test_compare_positions_reports_both_sides():
    out = reconcile.compare_positions(
        [{"ticker": "A", "qty": 10}, {"ticker": "B", "qty": 5}], [_pos("A", 10), _pos("C", 3)]
    )
    assert out == [
        {"ticker": "B", "ledger_qty": 5, "broker_qty": 0},
        {"ticker": "C", "ledger_qty": 0, "broker_qty": 3},
    ]


def test_reconcile_account_ok_when_ledger_matches(project, conn):
    acct = make_account("paper-main", "paper", "auto", ["KR"])
    _snap(conn)
    notes = Notes()
    out = reconcile.reconcile_account(
        conn, _broker(acct, [_pos("005930", 10)]), acct, "KR",
        today=D, now=lambda: T0, notifier=notes,
    )  # fmt: skip
    assert out["ok"] and out["mismatches"] == [] and out["open_orders"] == []
    assert notes.sent == [] and not out["snapshot_fixed"]
    assert control.get_flag(conn, reconcile.flag_key("paper-main", "KR", D))["ok"] is True


def test_reconcile_mismatch_notifies_and_fixes_snapshot_but_keeps_orders(project, conn):
    acct = make_account("paper-main", "paper", "auto", ["KR"])
    _snap(conn)
    oid = _order(conn)
    notes = Notes()
    out = reconcile.reconcile_account(
        conn, _broker(acct, [_pos("005930", 7), _pos("000660", 2)]), acct, "KR",
        today=D, now=lambda: T0, notifier=notes, label=lambda t: f"[{t}]",
    )  # fmt: skip
    assert not out["ok"] and out["snapshot_fixed"]
    assert out["mismatches"] == [
        {"ticker": "000660", "ledger_qty": 0, "broker_qty": 2},
        {"ticker": "005930", "ledger_qty": 10, "broker_qty": 7},
    ]
    assert [o["id"] for o in out["open_orders"]] == [oid]
    assert len(notes.sent) == 1 and notes.sent[0].startswith(
        "[대조 불일치] paper-main · KR 2026-10-24"
    )
    assert "[005930] 원장 10↔KIS 7" in notes.sent[0] and f"주문 #{oid} BUY" in notes.sent[0]
    latest = latest_positions(conn, "paper-main", "KR")
    assert {p["ticker"]: p["qty"] for p in latest["positions"]} == {"005930": 7, "000660": 2}
    assert latest["nav"] == "2000000"
    row = conn.execute("SELECT status FROM orders WHERE id=?", (oid,)).fetchone()
    assert row["status"] == "accepted"


def test_reconcile_notifies_once_per_account_market_day_and_keeps_orders(project, conn):
    acct = make_account("paper-main", "paper", "auto", ["KR"])
    _snap(conn)
    oid = _order(conn)
    broker = _broker(acct, [_pos("005930", 7)])
    notes = Notes()
    before = conn.execute("SELECT * FROM orders").fetchall()
    for _ in range(2):
        reconcile.reconcile_account(
            conn, broker, acct, "KR", today=D, now=lambda: T0, notifier=notes
        )
    assert len(notes.sent) == 1
    latest = latest_positions(conn, "paper-main", "KR")
    assert [(p["ticker"], p["qty"]) for p in latest["positions"]] == [("005930", 7)]
    assert [dict(r) for r in conn.execute("SELECT * FROM orders")] == [dict(r) for r in before]
    assert oid == before[0]["id"]


def test_reconcile_all_isolates_failures_and_filters_market(project, conn):
    a1 = make_account("paper-main", "paper", "auto", ["KR", "US"])
    a2 = make_account("real-main", "real", "off", ["KR"])
    b1, b2 = _broker(a1, []), _broker(a2, [])
    b2.fail = {"get_balance", "get_positions"}
    out = reconcile.reconcile_all(
        conn, {"paper-main": b1, "real-main": b2}, [a1, a2],
        market="KR", today_for=lambda m: D, now=lambda: T0,
    )  # fmt: skip
    assert out["paper-main:KR"]["ok"] and "error" in out["real-main:KR"]
    assert "paper-main:US" not in out


def test_reconcile_body_runs_all_accounts_for_market(project, conn, settings):
    a = make_account("paper-main", "paper", "off", ["KR", "US"])
    cash = {
        m: Cash(currency=c, available=Decimal(1), total=Decimal(1), nav=Decimal(2))
        for m, c in (("KR", "KRW"), ("US", "USD"))
    }
    broker = FakeBroker(to_broker_account(a), cash=cash)
    deps = SimpleNamespace(
        settings=settings, conn_factory=db.connect, state=None,
        now=lambda: datetime(2026, 10, 8, 7, 40, tzinfo=UTC), notifier=None,
        markets=["KR", "US"], stop_event=threading.Event(),
        ports={"accounts": make_port([a], {"paper-main": broker})},
    )  # fmt: skip
    out = spot_jobs.reconcile_body(deps, SimpleNamespace(market="KR"))
    assert list(out) == ["paper-main:KR"] and out["paper-main:KR"]["ok"] is True
    both = spot_jobs.reconcile_request(deps, {"market": None}, lambda t: None)
    assert set(both) == {"paper-main:KR", "paper-main:US"}


def test_reconcile_declarations():
    r = spot_jobs.RECONCILE
    assert (r.id, r.kind, r.per_market, r.schedule_key, r.gate, r.catch_up) == (
        "{market}_reconcile", "reconcile", True, "reconcile.{market}", "session", "queue",
    )  # fmt: skip
    assert (r.days, r.trigger, r.misfire) == ("mon-fri", "cron", "idempotent")
    b = spot_jobs.BALANCE_REFRESH
    assert (b.trigger, b.schedule_key, b.gate, b.catch_up) == (
        "interval", "balance_refresh.interval_s", "none", "none",
    )  # fmt: skip
    q = {k.kind: k for k in spot_jobs.QUEUE_KINDS}["reconcile"]
    assert (q.label, q.lane, q.priority, q.trading, q.restart_requeue) == (
        "대사", "fast", 0, False, True,
    )  # fmt: skip


def test_latest_positions_uses_nav_date_after_full_liquidation(project, conn):
    acct = make_account("paper-main", "paper", "auto", ["KR"])
    y = date(2026, 10, 23)
    xledger.snapshot_positions(
        conn, account_id="paper-main", d=y, market="KR", nav=Decimal(1), cash=Decimal(1),
        positions=[{"ticker": "005930", "market": "KR", "qty": 10}],
    )  # fmt: skip
    xledger.snapshot_positions(
        conn, account_id="paper-main", d=D, market="KR", nav=Decimal(2), cash=Decimal(2),
        positions=[],
    )  # fmt: skip
    latest = latest_positions(conn, "paper-main", "KR")
    assert latest["date"] == D.isoformat() and latest["positions"] == []
    out = reconcile.reconcile_account(
        conn, _broker(acct, []), acct, "KR", today=D, now=lambda: T0, notifier=Notes()
    )
    assert out["ok"] and out["mismatches"] == []


def test_reconcile_notified_only_when_sent(project, conn):
    acct = make_account("paper-main", "paper", "auto", ["KR"])
    _snap(conn)
    notes = Notes()
    ok = _broker(acct, [_pos("005930", 10)])
    bad = _broker(acct, [_pos("005930", 7)])
    kw = dict(today=D, now=lambda: T0, notifier=notes)
    reconcile.reconcile_account(conn, ok, acct, "KR", **kw)
    assert notes.sent == []
    reconcile.reconcile_account(conn, bad, acct, "KR", **kw)  # ok 뒤 같은 날 불일치
    assert len(notes.sent) == 1
    xledger.snapshot_positions(
        conn, account_id="paper-main", d=D, market="KR", nav=Decimal(1), cash=Decimal(1),
        positions=[{"ticker": "005930", "market": "KR", "qty": 3}],
    )  # fmt: skip
    reconcile.reconcile_account(conn, bad, acct, "KR", **kw)  # 불일치 → 불일치
    assert len(notes.sent) == 1


def test_reconcile_failed_send_retries(project, conn):
    acct = make_account("paper-main", "paper", "auto", ["KR"])
    _snap(conn)

    class Boom:
        def send(self, text, **_):
            raise RuntimeError("down")

    bad = _broker(acct, [_pos("005930", 7)])
    kw = dict(today=D, now=lambda: T0)
    reconcile.reconcile_account(conn, bad, acct, "KR", notifier=Boom(), **kw)
    xledger.snapshot_positions(
        conn, account_id="paper-main", d=D, market="KR", nav=Decimal(1), cash=Decimal(1),
        positions=[{"ticker": "005930", "market": "KR", "qty": 3}],
    )  # fmt: skip
    notes = Notes()
    reconcile.reconcile_account(conn, bad, acct, "KR", notifier=notes, **kw)
    assert len(notes.sent) == 1


def test_reconcile_schedule_accepts_string_and_object():
    from trader.features.spot.settings import ReconcileSchedule

    s = ReconcileSchedule.model_validate({"kr": "15:50", "us": {"time": "16:20", "tz": "UTC"}})
    assert s.kr == "15:50" and s.us.time == "16:20" and s.us.tz == "UTC"
    d = ReconcileSchedule()
    assert d.kr == "15:45" and d.us.tz == "America/New_York"
