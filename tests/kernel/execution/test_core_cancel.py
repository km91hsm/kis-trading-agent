"""core.cancel: 브로커 취소 1회 + 상태 갱신 (dry_run 은 기록만, 실패는 상태 유지 후 재발생)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trader.kernel.broker.models import OrderResult
from trader.kernel.execution import core
from trader.kernel.execution.idem import ExecKeyStore
from trader.kernel.execution.ledger import insert_order

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)


class _Notifier:
    def send(self, text, *, escape=True):
        return True


class _Broker:
    def __init__(self, exc=None, accepted=True):
        self.calls: list[tuple[str, dict]] = []
        self.exc = exc
        self.accepted = accepted

    def cancel_order(self, order_no, **kw):
        self.calls.append((order_no, kw))
        if self.exc:
            raise self.exc
        return OrderResult(order_no=order_no, accepted=self.accepted, message="거부됨")


def _deps(conn):
    return core.ExecDeps(
        conn=conn,
        notifier=_Notifier(),
        now=lambda: T0,
        idem=ExecKeyStore(conn),
        broker_of=lambda a: None,
    )


def _order(conn):
    return insert_order(
        conn,
        decision_id=None,
        account_id="paper-main",
        ticker="005930",
        market="KR",
        side="BUY",
        qty=2,
        order_type="LIMIT",
        price=Decimal("100"),
        status="accepted",
        order_no="0001",
        message="",
        mode="auto",
    )


def _status(conn, oid):
    return conn.execute("SELECT status FROM orders WHERE id=?", (oid,)).fetchone()["status"]


def test_cancel_calls_broker_once_and_sets_cancelled(conn):
    oid, broker = _order(conn), _Broker()
    out = core.cancel(
        _deps(conn), order_id=oid, order_no="0001", market="KR", broker=broker,
        dry_run=False, reason="재호가", exchange="NAS",
    )  # fmt: skip
    assert out == {"ok": True, "order_id": oid, "status": "cancelled"}
    assert broker.calls == [
        ("0001", {"market": "KR", "org_no": None, "ticker": "005930", "exchange": "NAS", "qty": 2})
    ]
    assert _status(conn, oid) == "cancelled"


def test_cancel_dry_run_records_without_broker(conn):
    oid, broker = _order(conn), _Broker()
    out = core.cancel(
        _deps(conn), order_id=oid, order_no="0001", market="KR", broker=broker,
        dry_run=True, reason="재호가",
    )  # fmt: skip
    assert out == {"ok": True, "dry_run": True, "order_id": oid, "status": "accepted"}
    assert broker.calls == []
    assert _status(conn, oid) == "accepted"


def test_cancel_broker_failure_keeps_status_and_raises(conn):
    oid, broker = _order(conn), _Broker(exc=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        core.cancel(
            _deps(conn), order_id=oid, order_no="0001", market="KR", broker=broker,
            dry_run=False, reason="재호가",
        )  # fmt: skip
    assert len(broker.calls) == 1
    assert _status(conn, oid) == "accepted"


def test_cancel_rejected_keeps_status_and_returns_not_ok(conn):
    oid, broker = _order(conn), _Broker(accepted=False)
    out = core.cancel(
        _deps(conn), order_id=oid, order_no="0001", market="KR", broker=broker,
        dry_run=False, reason="재호가",
    )  # fmt: skip
    assert out == {"ok": False, "order_id": oid, "status": "accepted", "message": "거부됨"}
    assert _status(conn, oid) == "accepted"


def test_cancel_missing_order_raises(conn):
    with pytest.raises(ValueError, match="없음"):
        core.cancel(
            _deps(conn), order_id=999, order_no="x", market="KR", broker=_Broker(),
            dry_run=False, reason="r",
        )  # fmt: skip


@pytest.mark.parametrize("dead", ["rejected", "cancelled", "failed", "expired"])
def test_cancel_dead_order_skips_broker(conn, dead):
    """이미 끝난 주문(DEAD_STATUSES)은 브로커를 부르지 않고 ok=False."""
    oid, broker = _order(conn), _Broker()
    conn.execute("UPDATE orders SET status=? WHERE id=?", (dead, oid))
    out = core.cancel(
        _deps(conn), order_id=oid, order_no="0001", market="KR", broker=broker,
        dry_run=False, reason="재호가",
    )  # fmt: skip
    assert out["ok"] is False and out["status"] == dead and out["message"]
    assert broker.calls == []
    assert _status(conn, oid) == dead
