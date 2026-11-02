"""승인 지문 — 요청 뒤 주문 내용이 바뀌면 실행하지 않는다.

옛 `tests/orchestrator/test_approval_fingerprint.py` 에서 이식. v5 `approvals.fingerprint` 는
NOT NULL 이라 옛 `test_approve_legacy_null_fingerprint_passes` 는
`test_approval_without_fingerprint_cannot_be_created` 로 바꿨다(Ruling 15)."""

import json
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.features.spot.conftest import make_account
from tests.features.spot.helpers import ok_decision
from trader.features.accounts import api as accounts_api
from trader.features.accounts.settings import to_broker_account
from trader.features.spot import executor
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.schemas import Decision
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash
from trader.kernel.execution import ledger as repo

T0 = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)
D = date(2026, 10, 23)


class Sent:
    def __init__(self):
        self.sent = []

    def send(self, text, **kw):
        self.sent.append(text)
        return True


@pytest.fixture
def ectx(conn, project, settings):
    acct = make_account("paper-main", "paper", "approve")
    broker = FakeBroker(
        to_broker_account(acct),
        cash={"KR": Cash(currency="KRW", available=Decimal("10000000"), total=Decimal("10000000"))},
    )
    return executor.ExecContext(
        settings=settings,
        accounts=[acct],
        conn=conn,
        brokers={acct.id: broker},
        policy=accounts_api.effective_mode,
        paused=accounts_api.is_paused,
        notifier=Sent(),
        now=lambda: T0,
    )


def _request(ectx, conn):
    dec = Decision(**ok_decision())
    spot_ledger.ingest_checkpoint(
        conn, D, "KR", "005930", "30_decision", {"data": dec.model_dump()}
    )
    executor.execute(ectx, dec, date=D, market="KR", price=Decimal("71000"), dry_run=False)
    (a,) = repo.pending_approvals(conn)
    return a


def test_fingerprint_stable_key_order():
    a = executor.approval_fingerprint("acct", 3, {"ticker": "005930", "qty": 2, "price": "1"})
    b = executor.approval_fingerprint("acct", 3, {"price": "1", "qty": 2, "ticker": "005930"})
    assert a == b and len(a) == 64
    assert a != executor.approval_fingerprint(
        "acct", 3, {"ticker": "005930", "qty": 3, "price": "1"}
    )
    # 표시용 이름은 지문에 들어가지 않는다
    assert a == executor.approval_fingerprint(
        "acct", 3, {"ticker": "005930", "qty": 2, "price": "1", "name": "x"}
    )


def test_approve_rejects_tampered_qty(ectx, conn):
    a = _request(ectx, conn)
    row = conn.execute(
        "SELECT fingerprint, order_json FROM approvals WHERE id=?", (a["id"],)
    ).fetchone()
    assert row["fingerprint"]
    order = json.loads(row["order_json"])
    order["qty"] = int(order["qty"]) * 10
    conn.execute("UPDATE approvals SET order_json=? WHERE id=?", (json.dumps(order), a["id"]))
    conn.commit()
    r = executor.approve(ectx, a["id"])
    assert r["ok"] is False and "지문" in r["error"]
    assert ectx.brokers["paper-main"].orders == []
    assert repo.get_approval(conn, a["id"])["status"] == "failed"
    assert any("승인 지문 불일치" in m for m in ectx.notifier.sent)


def test_approval_without_fingerprint_cannot_be_created(ectx, conn):
    """v5 는 지문 없는(NULL) 승인 행을 허용하지 않는다 — 옛 '지문 없는 행은 통과' 경로가 없다."""
    a = _request(ectx, conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE approvals SET fingerprint=NULL WHERE id=?", (a["id"],))
    conn.rollback()
    assert repo.get_approval(conn, a["id"])["fingerprint"]


def test_approve_untampered_passes(ectx, conn):
    a = _request(ectx, conn)
    assert executor.approve(ectx, a["id"])["ok"] is True
