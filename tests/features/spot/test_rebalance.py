"""주간 리밸런스 — 밴드 밖만, TRIM 먼저, 결정 행 없음, 게이트 그대로.

옛 `tests/orchestrator/test_rebalance.py` 15 이식 — 치환만: `Settings(rebalance=…)`→
`settings.model_copy`(`RebalanceCfg`), `executor.ExecContext`→`conftest.ectx`(포트·정책 주입),
`FakeBroker(acct)`→`to_broker_account`, `control.set_flag("paused")`→`modes.set_paused`,
`repo.create_approval`→`xledger.create_approval`(지문 인자), `decisions.json`→`data_json`,
옛 `test_daemon_registers_friday_rebalance_cron`→`daemon.jobs.build`.
신규: 큐 kind·본문·제외·손절선."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.features.spot.approval_helpers import Sent
from tests.features.spot.conftest import ectx, make_account
from trader.features.accounts import modes
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.accounts.settings import to_broker_account
from trader.features.spot import executor, rebalance
from trader.features.spot import jobs as spot_jobs
from trader.features.spot.feature import FEATURE
from trader.features.spot.settings import CashParkCfg, RebalanceCfg
from trader.kernel.broker.fake import FakeBroker, FakeMarket
from trader.kernel.broker.models import Cash, Position
from trader.kernel.cli.boot import build_registry
from trader.kernel.config import Secrets
from trader.kernel.config.loader import load
from trader.kernel.daemon import jobs as daemon_jobs
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import control, db
from trader.kernel.registry import discover
from trader.kernel.session.run_store import read_checkpoint, run_date_for, run_dir

D = date(2026, 10, 30)  # 금요일
NOW = datetime(2026, 10, 30, 5, 30, tzinfo=UTC)


def _pos(t, qty, px="100000"):
    p = Decimal(px)
    return Position(
        ticker=t,
        market="KR",
        qty=qty,
        avg_price=p,
        current_price=p,
        eval_amount=p * qty,
        pnl_pct=0.0,
        sellable_qty=qty,
    )


class P:
    def __init__(self, ev):
        self.eval_amount = Decimal(ev)


def test_only_beyond_band():
    legs = rebalance.drift_legs(
        {"A": P("300"), "B": P("120")}, Decimal("1000"), {"A": 0.2, "B": 0.1}, band=0.05
    )
    assert [(x.ticker, x.action) for x in legs] == [("A", "TRIM")]  # B 는 +2%p 라 밴드 안


def test_trim_before_add():
    legs = rebalance.drift_legs(
        {"A": P("100"), "B": P("400"), "C": P("320")},
        Decimal("1000"),
        {"A": 0.25, "B": 0.25, "C": 0.2},
        band=0.05,
    )
    assert [(x.ticker, x.action) for x in legs] == [("B", "TRIM"), ("C", "TRIM"), ("A", "ADD")]


def test_excludes_hedge_and_scalp():
    legs = rebalance.drift_legs(
        {"A": P("400"), "H": P("400")}, Decimal("1000"), {"A": 0.1, "H": 0.1}, exclude={"H"}
    )
    assert [x.ticker for x in legs] == ["A"]


def _decision(conn, ticker, action, weight, d="2026-10-27", source="llm", stop=90000):
    data = {
        "ticker": ticker,
        "action": action,
        "target_weight": weight,
        "order_type": "LIMIT",
        "limit_price": 100000,
        "stop_loss": stop,
        "take_profit": 120000,
        "horizon_days": 20,
        "confidence": 0.7,
        "rationale": "r",
        "invalidation_conditions": [],
    }
    conn.execute(
        "INSERT INTO decisions(date, market, ticker, action, target_weight, data_json, "
        "created_at, source) VALUES (?,?,?,?,?,?,?,?)",
        (d, "KR", ticker, action, weight, json.dumps(data), f"{d}T00:00:00+00:00", source),
    )
    conn.commit()


def test_hold_decision_has_no_target(conn):
    _decision(conn, "005930", "HOLD", 0.0)
    assert rebalance.target_decision(conn, "KR", "005930", D, 30) is None
    _decision(conn, "000660", "BUY", 0.1, d="2026-08-01")
    assert rebalance.target_decision(conn, "KR", "000660", D, 30) is None  # 너무 오래됨
    _decision(conn, "035420", "BUY", 0.1)
    assert float(rebalance.target_decision(conn, "KR", "035420", D, 30)["target_weight"]) == 0.1


CASH = {
    "KR": Cash(
        currency="KRW",
        available=Decimal("5000000"),
        total=Decimal("5000000"),
        nav=Decimal("10000000"),
    )
}


@pytest.fixture
def ctx(conn, project, settings, ports):
    acct = make_account("paper-main", "paper", "auto", ["KR"])
    # NAV 1천만: 005930 30%(목표 15%) → TRIM, 000660 2%(목표 10%) → ADD
    broker = FakeBroker(
        to_broker_account(acct), positions=[_pos("005930", 30), _pos("000660", 2)], cash=CASH
    )
    c = ectx(ports, settings, conn, [acct], {acct.id: broker})
    c.now = lambda: NOW
    return c


def _seed(conn):
    _decision(conn, "005930", "BUY", 0.15)
    _decision(conn, "000660", "BUY", 0.10)
    # 최소 보유 3거래일을 넘기도록 오래전 매수 체결
    for t in ("005930", "000660"):
        conn.execute(
            "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, status, mode, "
            "created_at) VALUES ('paper-main',?,'KR','BUY',1,'LIMIT','filled','auto',?)",
            (t, (NOW - timedelta(days=20)).isoformat()),
        )
    conn.commit()


def _q(t):
    return Decimal("100000")


def _run(ctx, **kw):
    kw.setdefault("dry_run", False)
    kw.setdefault("quote_fn", _q)
    return rebalance.run(ctx, market="KR", today=D, **kw)


def test_rebalance_stage_names_and_null_decision(ctx, conn):
    _seed(conn)
    out = _run(ctx)
    assert [(o["ticker"], o["action"]) for o in out["orders"]] == [
        ("005930", "TRIM"),
        ("000660", "ADD"),
    ]
    b = ctx.brokers["paper-main"]
    assert [(o.ticker, o.side) for o in b.orders] == [("005930", "SELL"), ("000660", "BUY")]
    assert b.orders[0].qty == 15  # 30% → 15%
    d = run_dir(D, "KR", "005930")
    assert read_checkpoint(d, "43_rebalance_paper-main")["data"]["approved"] is True
    assert read_checkpoint(d, "53_order_rebalance_paper-main")["data"]["status"] == "accepted"
    assert read_checkpoint(d, "40_risk_check_paper-main") is None
    rows = conn.execute(
        "SELECT decision_id FROM orders WHERE created_at >= ?", (NOW.isoformat(),)
    ).fetchall()
    assert rows and all(r["decision_id"] is None for r in rows)


def test_rebalance_runs_once_per_day(ctx, conn):
    _seed(conn)
    _run(ctx)
    assert _run(ctx) == {"skipped": "오늘 이미 실행"}


def test_rebalance_skips_while_paused_without_marking_done(ctx, conn):
    """전체 검토 2차 2-O15: 일시정지 중엔 판정도 플래그도 남기지 않는다 — 재개 뒤 같은 주의
    리밸런스가 조용히 사라지지 않게(reprice·cash_park 와 같은 순서)."""
    _seed(conn)
    modes.set_paused(conn, True)
    assert _run(ctx) == {"skipped": "일시정지"}
    assert control.get_flag(conn, rebalance.done_key("KR", D)) is None
    assert ctx.brokers["paper-main"].orders == []
    modes.set_paused(conn, False)
    assert "legs" in _run(ctx)


def test_rebalance_skips_open_order_today(ctx, conn):
    _seed(conn)
    conn.execute(
        "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, status, mode, "
        "created_at) VALUES ('paper-main','005930','KR','SELL',1,'LIMIT','accepted','auto',?)",
        (NOW.isoformat(),),
    )
    conn.commit()
    out = _run(ctx)
    assert any(leg.get("skipped") for leg in out["legs"] if leg["ticker"] == "005930")
    assert [o.ticker for o in ctx.brokers["paper-main"].orders] == ["000660"]


def test_rebalance_skips_filled_order_earlier_today(ctx, conn):
    """Ruling P9: 오늘 이미 체결된 같은 방향 주문이 있는 레그도 다시 내지 않는다(옛 동작)."""
    _seed(conn)
    conn.execute(
        "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, status, mode, "
        "created_at) VALUES ('paper-main','005930','KR','SELL',1,'LIMIT','filled','auto',?)",
        ((NOW - timedelta(hours=1)).isoformat(),),
    )
    conn.commit()
    out = _run(ctx)
    assert any(leg.get("skipped") for leg in out["legs"] if leg["ticker"] == "005930")
    assert [o.ticker for o in ctx.brokers["paper-main"].orders] == ["000660"]


def test_rebalance_respects_gate_rejections(ctx, conn):
    _seed(conn)
    conn.execute(
        "UPDATE orders SET created_at=? WHERE side='BUY'", ((NOW - timedelta(hours=1)).isoformat(),)
    )
    conn.commit()  # 오늘 매수 → TRIM 은 최소 보유 미충족, ADD 는 당일 중복 매수로 게이트 거부
    out = _run(ctx)
    rs = {o["ticker"]: o["results"][0] for o in out["orders"]}
    assert rs["005930"]["approved"] is False and any(
        "최소 보유" in r for r in rs["005930"]["reasons"]
    )
    assert ctx.brokers["paper-main"].orders == []


def test_rebalance_dry_run_no_orders(ctx, conn):
    _seed(conn)
    out = _run(ctx, dry_run=True)
    assert out["orders"] and ctx.brokers["paper-main"].orders == []
    # dry-run 은 완료 표시를 남기지 않는다 — 같은 날 실제 실행이 막히지 않게(전체 검토 2026-10-27)
    assert control.get_flag(conn, rebalance.done_key("KR", D)) is None
    real = _run(ctx)
    assert "skipped" not in real and ctx.brokers["paper-main"].orders


def test_disabled_skips(ctx, conn):
    ctx.settings = ctx.settings.model_copy(update={"rebalance": RebalanceCfg(enabled=False)})
    assert _run(ctx, dry_run=True, quote_fn=lambda t: 1)["skipped"]


def test_daemon_registers_friday_rebalance_cron(project, settings):
    schedule = load("schedule", build_registry(discover()), path=project / "config" / "x.yaml")
    ctx = SimpleNamespace(gates={"market": None, "monitor": None})

    def built(s):
        out = daemon_jobs.build(
            [FEATURE], schedule=schedule, settings=s, markets=["KR", "US"], ctx=ctx
        )
        return {j.id: j for j in out if j.spec.kind == "rebalance"}

    got = built(settings)
    assert got["kr_rebalance"].cron == {
        "hour": 14, "minute": 30, "day_of_week": "fri",
    }  # fmt: skip
    us = got["us_rebalance"]
    assert (us.cron["hour"], us.cron["minute"], us.cron["day_of_week"]) == (15, 0, "fri")
    assert str(us.trigger_kwargs["timezone"]) == "America/New_York"  # 그 시장 스케줄의 tz
    off = settings.model_copy(update={"rebalance": RebalanceCfg(enabled=False)})
    assert built(off) == {}


def test_rebalance_skips_pending_approval(ctx, conn):
    """approve 계좌에 같은 감축의 pending 승인이 있으면 리밸런스가 두 번째 요청을 만들지 않는다."""
    _seed(conn)
    did = conn.execute("SELECT id FROM decisions WHERE ticker='005930'").fetchone()[0]
    xledger.create_approval(
        conn,
        decision_id=did,
        account_id="paper-main",
        order={"ticker": "005930", "market": "KR", "side": "SELL", "qty": 15},
        requested_at=(NOW - timedelta(minutes=5)).isoformat(),
        expires_at=(NOW + timedelta(minutes=25)).isoformat(),
        fingerprint="fp",
    )
    out = _run(ctx)
    assert any(leg.get("skipped") for leg in out["legs"] if leg["ticker"] == "005930")
    assert [o.ticker for o in ctx.brokers["paper-main"].orders] == ["000660"]


def test_rebalance_add_passes_change_pct_to_chase_gate(ctx, conn):
    """시세의 당일 등락률이 게이트로 가서 급등 종목 ADD 는 추격 금지로 거부된다."""
    _seed(conn)
    q = SimpleNamespace(price=Decimal("100000"), change_pct=0.12)
    out = _run(ctx, quote_fn=lambda t: q)
    rs = {o["ticker"]: o["results"][0] for o in out["orders"]}
    assert rs["000660"]["approved"] is False
    assert any("급등" in r for r in rs["000660"]["reasons"])
    assert [o.side for o in ctx.brokers["paper-main"].orders] == ["SELL"]  # TRIM 은 면제


def test_rebalance_keeps_zero_confidence(ctx, conn, monkeypatch):
    _seed(conn)
    conn.execute(
        "UPDATE decisions SET data_json=json_set(data_json, '$.confidence', 0.0) "
        "WHERE ticker='000660'"
    )
    conn.commit()
    seen = []
    real = executor.execute

    def spy(ctx_, dec, **kw):
        seen.append((dec.ticker, dec.confidence))
        return real(ctx_, dec, **kw)

    monkeypatch.setattr(executor, "execute", spy)
    _run(ctx, dry_run=True)
    assert ("000660", 0.0) in seen


# --- 신규: Review Focus 5 · 제외 · 손절선 · 잡/큐 -------------------------------------------


def test_rebalance_add_skipped_at_or_below_stop_line(ctx, conn):
    """ADD 는 현재가가 손절선 위일 때만 — 손절선 이하로 떨어진 종목을 더 사지 않는다."""
    _seed(conn)  # 000660 손절선 90000
    out = _run(ctx, quote_fn=lambda t: Decimal("90000"))
    add = next(o for o in out["orders"] if o["ticker"] == "000660")
    assert "손절선" in add["skipped"] and "action" not in add
    assert [o.side for o in ctx.brokers["paper-main"].orders] == ["SELL"]


def test_rebalance_removes_excluded_tickers_from_legs(ctx, conn, settings):
    """헤지·현금 운용 ETF(`excluded_tickers`)는 보유가 목표에서 벗어나도 레그에서 빠진다."""
    _seed(conn)
    ctx.settings = settings.model_copy(
        update={"cash_park": CashParkCfg(enabled=True, ticker="005930")}
    )
    out = _run(ctx)
    assert [leg["ticker"] for leg in out["legs"]] == ["000660"]
    assert [o.ticker for o in ctx.brokers["paper-main"].orders] == ["000660"]


class _Port(AccountsPortImpl):
    def __init__(self, accounts, brokers, market):
        super().__init__(accounts, Secrets())
        self._brokers, self._market = brokers, market

    def brokers(self):
        return self._brokers

    def market_client(self):
        return self._market


def _deps(settings, ports, port, notifier=None, dry_run=False):
    return SimpleNamespace(
        settings=settings,
        conn_factory=db.connect,
        state=None,
        now=lambda: NOW,
        notifier=notifier,
        dry_run=dry_run,
        ports={**ports, "accounts": port},
        markets=["KR"],
        queue=None,
    )


def _port(market):
    acct = make_account("paper-main", "paper", "auto", ["KR"])
    broker = FakeBroker(
        to_broker_account(acct), positions=[_pos("005930", 30), _pos("000660", 2)], cash=CASH
    )
    return _Port([acct], {acct.id: broker}, market), broker


PRICES = {"005930": Decimal("100000"), "000660": Decimal("100000")}


def test_rebalance_declarations():
    s = spot_jobs.REBALANCE
    assert (s.id, s.kind, s.trigger, s.per_market, s.schedule_key) == (
        "{market}_rebalance", "rebalance", "cron", True, "rebalance.{market}",
    )  # fmt: skip
    assert (s.gate, s.heavy, s.misfire, s.catch_up, s.days) == (
        "market", False, "idempotent", "queue", "fri",
    )  # fmt: skip
    assert s.body is rebalance.rebalance_body and s in FEATURE.jobs
    k = next(k for k in spot_jobs.QUEUE_KINDS if k.kind == "rebalance")
    assert (k.label, k.lane, k.priority, k.trading, k.restart_requeue) == (
        "리밸런스", "fast", 0, True, False,
    )  # fmt: skip
    assert k.body is rebalance.rebalance_request


def test_rebalance_body_without_market_client_skips(project, conn, settings, ports):
    port, broker = _port(None)
    out = rebalance.rebalance_body(_deps(settings, ports, port), SimpleNamespace(market="KR"))
    assert out == {"skipped": "시세 클라이언트 없음"} and broker.orders == []


def test_rebalance_body_orders_and_notifies(project, conn, settings, ports):
    _seed(conn)
    port, broker = _port(FakeMarket(prices=PRICES))
    sent = Sent()
    out = rebalance.rebalance_body(
        _deps(settings, ports, port, notifier=sent), SimpleNamespace(market="KR")
    )
    assert len(out["orders"]) == 2 and len(broker.orders) == 2
    assert any("리밸런스" in m and "2건" in m for m in sent.msgs)


def test_rebalance_queue_kind_skips_when_paused_without_done_flag(
    project, conn, settings, ports
):
    """Review Focus 5: 큐 kind 도 일시정지면 주문·done 플래그 없이 건너뛴다."""
    _seed(conn)
    modes.set_paused(conn, True)
    port, broker = _port(FakeMarket(prices=PRICES))
    out = rebalance.rebalance_request(_deps(settings, ports, port), {"market": "KR"}, None)
    assert out == {"KR": {"skipped": "일시정지"}} and broker.orders == []
    assert control.get_flag(conn, rebalance.done_key("KR", run_date_for("KR", NOW))) is None
    modes.set_paused(conn, False)
    again = rebalance.rebalance_request(_deps(settings, ports, port), {"market": "KR"}, None)
    assert len(again["KR"]["orders"]) == 2


def test_rebalance_injects_sector_of_from_scan_port(ctx, conn, monkeypatch):
    """M6 이연 해소: 리밸런스 Portfolio 에 `ports["scan"].sector_of` 가 들어간다(없으면 None)."""
    _seed(conn)
    got = []
    real = executor.build_portfolio

    def spy(*a, **kw):
        pf = real(*a, **kw)
        got.append(pf.sector_of)
        return pf

    monkeypatch.setattr(executor, "build_portfolio", spy)
    ctx.ports = {**ctx.ports, "scan": SimpleNamespace(sector_of=lambda m: {"005930": "반도체"}.get)}
    _run(ctx, dry_run=True)
    assert got and got[0]("005930") == "반도체" and got[0]("000660") is None
    got.clear()
    ctx.ports = {k: v for k, v in ctx.ports.items() if k != "scan"}
    _run(ctx, dry_run=True)
    assert got and all(s is None for s in got)


def test_run_excludes_scalp_open_tickers_via_port(ctx, conn):
    """A9: 단타가 들고 있는 종목은 리밸런스 다리에서 뺀다(`ScalpPort.open_tickers`)."""
    _seed(conn)
    ctx.ports = {**ctx.ports, "scalp": SimpleNamespace(open_tickers=lambda *a: {"005930"})}
    out = _run(ctx)
    assert all(leg["ticker"] != "005930" for leg in out["legs"])
    assert [o.ticker for o in ctx.brokers["paper-main"].orders] == ["000660"]
