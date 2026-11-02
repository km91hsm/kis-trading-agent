"""`approvals_expire` 잡: 만료분을 expired 로 바꾼다(boot 없이 DaemonDeps 로)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from tests.features.spot.approval_helpers import fake_brokers, make_account, make_pending
from trader.features.spot.jobs import APPROVALS_EXPIRE
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import db


def test_approvals_expire_job_expires_overdue(project, conn, settings):
    accounts = [make_account("paper-main", "paper", "approve")]
    t0 = datetime.now(UTC)
    aid = make_pending(conn, settings, accounts, fake_brokers(accounts), now=lambda: t0)
    spec = APPROVALS_EXPIRE
    assert (spec.id, spec.interval_s, spec.gate, spec.misfire) == (
        "approvals_expire", 60, "none", "idempotent",
    )  # fmt: skip
    deps = SimpleNamespace(
        settings=settings,
        conn_factory=db.connect,
        state=None,
        now=lambda: t0 + timedelta(minutes=31),
        notifier=None,
    )
    out = spec.body(deps, None)
    assert out == {"expired": [aid]}
    assert xledger.get_approval(conn, aid)["status"] == "expired"
    assert spec.body(deps, None) == {"expired": []}  # 멱등
