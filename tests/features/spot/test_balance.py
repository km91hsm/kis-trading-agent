"""balance 스냅샷 갱신: 장중 매 틱, 장 밖은 30분 넘은 스냅샷만, 계좌 실패 격리."""

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from tests.features.spot.approval_helpers import fake_brokers, make_account, make_port
from trader.features.spot import balance
from trader.features.spot import jobs as spot_jobs
from trader.kernel.broker.models import Position
from trader.kernel.ledger import db

SAT = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)  # 토요일 장 밖
MON_OPEN = datetime(2026, 10, 26, 1, 0, tzinfo=UTC)  # 월 10:00 KST


def _deps(settings, accounts, brokers, now):
    return SimpleNamespace(
        settings=settings,
        conn_factory=db.connect,
        state=None,
        now=now,
        notifier=None,
        markets=["KR"],
        stop_event=threading.Event(),
        ports={"accounts": make_port(accounts, brokers)},
    )


def _stamp(conn, at):
    conn.execute("UPDATE positions_snapshot SET created_at=?", (at.isoformat(),))
    conn.execute("UPDATE account_nav SET created_at=?", (at.isoformat(),))
    conn.commit()


def test_balance_refresh_every_tick_in_hours_and_off_hours_when_stale(project, conn, settings):
    accounts = [make_account("paper-main", "paper", "off")]
    brokers = fake_brokers(accounts)
    clock = {"t": SAT}
    deps = _deps(settings, accounts, brokers, lambda: clock["t"])
    body = spot_jobs.balance_body
    assert body(deps, None)["KR"]["refreshed"] == ["paper-main"]  # 스냅샷 없음 → 읽음
    _stamp(conn, SAT)
    clock["t"] = SAT + timedelta(minutes=10)
    assert body(deps, None)["KR"]["skipped"] == ["paper-main"]  # 장 밖 30분 안
    assert spot_jobs.balance_refresh(deps, force=True)["KR"]["refreshed"] == ["paper-main"]
    _stamp(conn, SAT)
    clock["t"] = SAT + timedelta(minutes=45)
    assert body(deps, None)["KR"]["refreshed"] == ["paper-main"]
    clock["t"] = MON_OPEN
    brokers["paper-main"].positions = [
        Position(
            ticker="999990", market="KR", qty=3, avg_price=Decimal("1000"),
            current_price=Decimal("1100"), eval_amount=Decimal("3300"), pnl_pct=10.0,
        )
    ]  # fmt: skip
    out = body(deps, None)
    assert out["KR"]["refreshed"] == ["paper-main"] and not brokers["paper-main"].orders
    n = conn.execute("SELECT COUNT(*) FROM positions_snapshot WHERE ticker='999990'").fetchone()[0]
    assert n == 1


def test_queued_fills_sync_refreshes_balance_right_after(project, conn, settings, monkeypatch):
    accounts = [make_account("paper-main", "paper", "off")]
    deps = _deps(settings, accounts, fake_brokers(accounts), lambda: SAT)
    monkeypatch.setattr(spot_jobs, "sync_all", lambda d: {"synced": 1})
    spot_jobs.balance_body(deps, None)  # 방금 스냅샷 — 주기 갱신이면 건너뛸 상태
    _stamp(conn, SAT)
    out = spot_jobs.fills_then_balance(deps, {}, lambda t: None)
    assert out["synced"] == 1 and out["balance"]["KR"]["refreshed"] == ["paper-main"]
    monkeypatch.setattr(spot_jobs, "balance_refresh", lambda d, force=False: 1 / 0)
    assert spot_jobs.fills_then_balance(deps, {}, lambda t: None) == {"synced": 1}


def test_refresh_only_stale_snapshots_in_hours(project, conn, settings):
    a = make_account("paper-main", "paper", "off")
    brokers = fake_brokers([a])
    kw = dict(accounts=[a], brokers=brokers, market="KR", settings=settings, now=MON_OPEN)
    assert balance.refresh(conn, **kw)["refreshed"] == ["paper-main"]
    _stamp(conn, MON_OPEN)
    assert balance.refresh(conn, only_stale=True, **kw)["skipped"] == ["paper-main"]
    later = {**kw, "now": MON_OPEN + timedelta(minutes=3)}
    assert balance.refresh(conn, only_stale=True, **later)["refreshed"] == ["paper-main"]
    assert balance.snapshot_age(conn, "paper-main", "KR", MON_OPEN) < 1
    assert balance.snapshot_age(conn, "none", "KR", MON_OPEN) == float("inf")
    assert balance.open_markets(["KR", "US"], MON_OPEN) == ["KR"]


def test_refresh_off_hours_every_30min(project, conn, settings):
    a = make_account("paper-main", "paper", "off")
    kw = dict(
        accounts=[a], brokers=fake_brokers([a]), market="KR", settings=settings,
        only_stale=True, stale_after=balance.OFF_HOURS_EVERY,
    )  # fmt: skip
    assert balance.refresh(conn, now=SAT, **kw)["refreshed"] == ["paper-main"]
    _stamp(conn, SAT)
    assert balance.refresh(conn, now=SAT + timedelta(minutes=29), **kw)["skipped"]
    assert balance.refresh(conn, now=SAT + timedelta(minutes=31), **kw)["refreshed"]


def test_refresh_isolates_broker_failure(project, conn, settings):
    a1 = make_account("paper-main", "paper", "off")
    a2 = make_account("real-main", "real", "off")
    brokers = fake_brokers([a1, a2])
    brokers["real-main"].fail = {"get_balance", "get_positions"}
    out = balance.refresh(
        conn, accounts=[a2, a1], brokers=brokers, market="KR", settings=settings, now=MON_OPEN
    )
    assert out["refreshed"] == ["paper-main"] and "real-main" in out["errors"]
