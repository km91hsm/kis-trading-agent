"""`approvals_expire` 잡: 만료분을 expired 로 바꾼다(boot 없이 DaemonDeps 로)."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.features.spot.approval_helpers import (
    fake_brokers,
    make_account,
    make_pending,
    make_port,
)
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.spot import fills as fills_mod
from trader.features.spot import jobs as spot_jobs
from trader.features.spot import monitor as monitor_mod
from trader.features.spot.feature import FEATURE
from trader.features.spot.jobs import APPROVALS_EXPIRE, QUEUE_KINDS, RebalanceParams
from trader.kernel.broker.fake import FakeMarket
from trader.kernel.config import Secrets
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
    q = {k.kind: k for k in spot_jobs.QUEUE_KINDS}["fills_sync"]
    assert (q.kind, q.lane, q.restart_requeue, q.body) == (
        "fills_sync", "fast", True, spot_jobs.fills_then_balance,
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


def test_fills_queue_body_still_syncs(project, conn, settings):
    accounts = [make_account("paper-main", "paper", "off")]
    deps = _fills_deps(settings, accounts, fake_brokers(accounts))
    deps.markets = ["KR"]
    out = spot_jobs.fills_then_balance(deps, {}, lambda t: None)
    assert "error" not in out and out["paper-main"]["failed_markets"] == []


def test_fills_body_errors_without_accounts_port(project, conn, settings):
    """계좌 포트가 없으면 'ok' 가 아니라 오류로 남는다. 포트가 있고 계좌가 0개면 정상(빈 결과)."""
    deps = _fills_deps(settings, [], {})
    deps.ports = {}
    out = spot_jobs.fills_body(deps, None)
    assert "계좌 포트 없음" in out["error"]
    assert spot_jobs.fills_then_balance(deps, {}, lambda t: None) == out
    assert spot_jobs.fills_body(_fills_deps(settings, [], {}), None) == {}


# --- {market}_monitor (옛 daemon `_monitor_body`) ---------------------------------------


class _MonitorPort(AccountsPortImpl):
    """합성 계좌 + 가짜 브로커·시세(네트워크 없음)."""

    def __init__(self, accounts, brokers, market):
        super().__init__(accounts, Secrets())
        self._brokers, self._market = brokers, market

    def brokers(self):
        return self._brokers

    def market_client(self):
        return self._market


def _monitor_deps(settings, port, now):
    return SimpleNamespace(
        settings=settings,
        conn_factory=db.connect,
        state=None,
        now=lambda: now,
        notifier=None,
        dry_run=False,
        ports={"accounts": port},
        queue=None,
    )


def test_monitor_declaration():
    s = spot_jobs.MONITOR
    assert (s.id, s.kind, s.trigger, s.per_market, s.schedule_key, s.interval_s) == (
        "{market}_monitor", "monitor", "interval", True, "{market}.monitor.interval_s", 60,
    )  # fmt: skip
    assert (s.gate, s.heavy, s.misfire, s.catch_up, s.start_soon_s) == (
        "monitor", False, "default", "none", 20,
    )  # fmt: skip
    assert s.body is spot_jobs.monitor_body and s in FEATURE.jobs


def test_monitor_body_without_market_client_skips(project, conn, settings, monkeypatch):
    accounts = [make_account("paper-main", "paper", "auto")]
    port = _MonitorPort(accounts, fake_brokers(accounts), None)
    called = []
    monkeypatch.setattr(monitor_mod, "monitor_once", lambda *a, **k: called.append(1))
    deps = _monitor_deps(settings, port, TUE_0900_KST)
    out = spot_jobs.monitor_body(deps, SimpleNamespace(market="KR"))
    assert out == {"skipped": "시세 클라이언트 없음"} and called == []


def test_monitor_body_uses_market_run_date(project, conn, settings, monkeypatch):
    """US 장중(KST 자정 넘김)에도 운영일은 뉴욕 날짜다 — 손절·재평가가 그날 analyze 와 같은
    runs/<date> 와 주문 창을 본다."""
    accounts = [make_account("paper-main", "paper", "auto", markets=["KR", "US"])]
    port = _MonitorPort(accounts, fake_brokers(accounts), FakeMarket())
    seen = {}

    def fake_once(ctx, *, market, today):
        seen.update(market=market, today=today, ctx=ctx)
        return {"checked": 0, "stop_loss_orders": 0, "triggers": 0, "reevaluated": 0}

    monkeypatch.setattr(monitor_mod, "monitor_once", fake_once)
    at = datetime(2026, 10, 27, 15, 30, tzinfo=UTC)  # 10-28 00:30 KST, 10-27 11:30 ET
    out = spot_jobs.monitor_body(_monitor_deps(settings, port, at), SimpleNamespace(market="US"))
    assert out["checked"] == 0
    assert seen["market"] == "US" and seen["today"] == date(2026, 10, 27)
    assert seen["ctx"].enqueue_reevaluate is not None and seen["ctx"].session_runner is not None
    spot_jobs.monitor_body(_monitor_deps(settings, port, at), SimpleNamespace(market="KR"))
    assert seen["today"] == date(2026, 10, 28)


# --- Review Focus 1: 데몬 경로(게이트 → monitor_body) ------------------------------------


class _Sent:
    def __init__(self):
        self.msgs: list[str] = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


def test_paused_daemon_monitor_tick_notifies_stop_paused_once(project, conn, settings):
    """Ruling 20(a): paused 상태의 데몬 monitor 틱은 게이트에서 건너뛰지 않고 `monitor_body` 가
    돈다 — 손절선 도달 종목은 주문 없이 `stop_paused` 를 두 틱에 걸쳐 1회만, 다른 종목 트리거는
    기록(재평가 큐 없음)."""
    import threading
    from decimal import Decimal

    from tests.features.spot.helpers import ok_decision
    from trader.features.accounts.gates import GATES
    from trader.features.spot import ledger as spot_ledger
    from trader.features.spot.schemas import Decision
    from trader.kernel.broker.models import Position
    from trader.kernel.cli.boot import build_registry
    from trader.kernel.config.loader import load
    from trader.kernel.daemon.jobs import build
    from trader.kernel.daemon.runner import DaemonDeps, run_job
    from trader.kernel.ledger import control
    from trader.kernel.platform.bundle import templates_dir
    from trader.kernel.registry import discover
    from trader.kernel.registry.types import Feature

    at = datetime(2026, 10, 27, 2, 0, tzinfo=UTC)  # 화 11:00 KST, KR 장중
    today = date(2026, 10, 27)
    for ticker, over in (
        ("005930", {"stop_loss": 65000.0}),
        ("000660", {"take_profit": 110000.0, "stop_loss": 90000.0}),
    ):
        dec = Decision(**ok_decision(ticker=ticker, **over))
        spot_ledger.ingest_checkpoint(
            conn, today, "KR", ticker, "30_decision", {"data": dec.model_dump()}
        )

    def pos(ticker, avg, cur):
        return Position(
            ticker=ticker, market="KR", qty=10, avg_price=Decimal(avg),
            current_price=Decimal(cur), eval_amount=Decimal(cur) * 10,
            pnl_pct=float(Decimal(cur) / Decimal(avg) - 1),
        )  # fmt: skip

    accounts = [make_account("paper-main", "paper", "auto")]
    brokers = fake_brokers(accounts)
    brokers["paper-main"].positions = [
        pos("005930", "70000", "63000"),
        pos("000660", "100000", "112000"),
    ]
    market = FakeMarket(prices={"005930": Decimal("63000"), "000660": Decimal("112000")})
    port = _MonitorPort(accounts, brokers, market)
    schedule = load(
        "schedule", build_registry(discover()), path=templates_dir() / "config" / "schedule.yaml"
    )
    sent = _Sent()
    deps = DaemonDeps(
        settings=settings, schedule=schedule, conn_factory=db.connect, state=project / "state",
        now=lambda: at, markets=["KR"], gates={}, stop_event=threading.Event(), dry_run=False,
        notifier=sent, ports={"accounts": port},
    )  # fmt: skip
    for g in GATES:
        deps.gates[g.name] = g.build(None, deps)
    control.set_flag(conn, "paused", True)
    feature = Feature(id="spot_monitor_only", tier="lane", jobs=(spot_jobs.MONITOR,))
    (job,) = build([feature], schedule=schedule, settings=settings, markets=["KR"], ctx=deps)
    outs = [run_job(conn, job, deps, stop_event=deps.stop_event) for _ in range(2)]
    assert all("skipped" not in o for o in outs), outs  # 게이트가 일시정지로 건너뛰지 않는다
    assert all(o["checked"] == 2 and o["stop_loss_orders"] == 0 for o in outs)
    paused_msgs = [m for m in sent.msgs if "일시정지" in m and "손절" in m]
    assert len(paused_msgs) == 1
    assert brokers["paper-main"].orders == [] and brokers["paper-main"].cancelled == []
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    rows = conn.execute("SELECT ticker, kind, handled_at FROM triggers").fetchall()
    assert {r["ticker"] for r in rows} == {"000660"} and all(r["handled_at"] is None for r in rows)
    assert "queued" not in outs[0]


def test_monitor_body_and_cli_fill_us_exchanges_from_ledger(project, conn, settings, monkeypatch):
    """S4: 잔고가 거래소(ovrs_excg_cd)를 주지 않은 US 보유도 `ticker_exchanges` 로 거래소를 얻는다
    — 데몬 `monitor_body` 가 `MonitorContext.exchanges` 를 채운다."""
    from decimal import Decimal

    from trader.features.spot import ledger as spot_ledger
    from trader.kernel.broker.models import Position

    spot_ledger.upsert_ticker_exchange(conn, "US", "KO", "NYS")
    assert spot_ledger.ticker_exchanges(conn, "US") == {"KO": "NYS"}
    assert spot_ledger.ticker_exchanges(conn, "KR") == {}
    accounts = [make_account("paper-main", "paper", "auto", markets=["US"])]
    port = _MonitorPort(accounts, fake_brokers(accounts), FakeMarket())
    seen = {}

    def fake_once(ctx, *, market, today):
        seen["ctx"] = ctx
        return {"checked": 0}

    monkeypatch.setattr(monitor_mod, "monitor_once", fake_once)
    at = datetime(2026, 10, 27, 15, 30, tzinfo=UTC)
    spot_jobs.monitor_body(_monitor_deps(settings, port, at), SimpleNamespace(market="US"))
    pos = Position(
        ticker="KO", market="US", qty=1, avg_price=Decimal("60"), current_price=Decimal("60"),
        eval_amount=Decimal("60"), pnl_pct=0.0,
    )  # fmt: skip
    assert pos.exchange is None
    assert monitor_mod._exchange(seen["ctx"], pos) == "NYS"


def test_rebalance_params_market_literal():
    assert RebalanceParams(market="KR").market == "KR"
    assert RebalanceParams().market is None
    with pytest.raises(ValidationError):
        RebalanceParams(market="JP")
    kinds = {q.kind: q for q in QUEUE_KINDS}
    assert kinds["rebalance"].params_model is RebalanceParams
