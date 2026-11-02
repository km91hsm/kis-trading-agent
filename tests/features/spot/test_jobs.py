"""`approvals_expire` 잡: 만료분을 expired 로 바꾼다(boot 없이 DaemonDeps 로)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from tests.features.spot.approval_helpers import (
    fake_brokers,
    make_account,
    make_pending,
    make_port,
)
from trader.features.spot import fills as fills_mod
from trader.features.spot import jobs as spot_jobs
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


# --- fills_sync (옛 tests/test_daemon_queue.py 에서 이식) --------------------------------

TUE_0900_KST = datetime(2026, 10, 27, 0, 0, tzinfo=UTC)


def _fills_deps(settings, accounts, brokers):
    return SimpleNamespace(
        settings=settings,
        conn_factory=db.connect,
        state=None,
        now=lambda: TUE_0900_KST,
        notifier=None,
        ports={"accounts": make_port(accounts, brokers)},
    )


def test_fills_sync_declaration():
    s = spot_jobs.FILLS_SYNC
    assert (s.id, s.kind, s.trigger, s.per_market, s.schedule_key, s.days) == (
        "fills_sync", "fills_sync", "cron", False, "fills_sync.times", "mon-sat",
    )  # fmt: skip
    assert (s.gate, s.misfire, s.catch_up) == ("none", "idempotent", "queue")
    (q,) = spot_jobs.QUEUE_KINDS
    assert (q.kind, q.lane, q.restart_requeue, q.body) == (
        "fills_sync", "fast", True, spot_jobs.fills_request,
    )  # fmt: skip


def test_fills_body_isolates_account_failure(project, conn, settings, monkeypatch):
    accounts = [
        make_account("real-main", "real", "off"),
        make_account("paper-main", "paper", "off"),
    ]
    deps = _fills_deps(settings, accounts, fake_brokers(accounts))

    def fake_sync(conn_, b, a, d, notifier=None, now=None):
        if a.id == "real-main":
            raise RuntimeError("db locked")
        return {"inserted": 0}

    monkeypatch.setattr(fills_mod, "sync_fills", fake_sync)
    out = spot_jobs.fills_body(deps, None)
    assert "error" in out["real-main"] and out["paper-main"] == {"inserted": 0}


def test_fills_failure_is_top_level_error(project, conn, settings):
    """체결 조회가 실패하면 잡이 'ok' 가 아니라 오류로 남는다(runs·하트비트·운영 점검에 보이게)."""
    accounts = [make_account("paper-main", "paper", "off")]
    brokers = fake_brokers(accounts)
    brokers["paper-main"].fail = {"get_fills"}
    out = spot_jobs.fills_body(_fills_deps(settings, accounts, brokers), None)
    assert "체결 조회 실패" in out["error"] and "paper-main KR" in out["error"]


def test_fills_request_is_sync_only(project, conn, settings):
    accounts = [make_account("paper-main", "paper", "off")]
    deps = _fills_deps(settings, accounts, fake_brokers(accounts))
    out = spot_jobs.fills_request(deps, {}, lambda t: None)
    assert "error" not in out and out["paper-main"]["failed_markets"] == []


def test_fills_body_errors_without_accounts_port(project, conn, settings):
    """계좌 포트가 없으면 'ok' 가 아니라 오류로 남는다. 포트가 있고 계좌가 0개면 정상(빈 결과)."""
    deps = _fills_deps(settings, [], {})
    deps.ports = {}
    out = spot_jobs.fills_body(deps, None)
    assert "계좌 포트 없음" in out["error"]
    assert spot_jobs.fills_request(deps, {}, lambda t: None) == out
    assert spot_jobs.fills_body(_fills_deps(settings, [], {}), None) == {}
