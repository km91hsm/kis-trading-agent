"""아침 미체결 재호가(Phase 23 B): 후보·추격 상한·취소→재제출·운영일 1회·dry_run/off 제외.

옛 `tests/orchestrator/test_reprice.py` 7건 + `tests/test_daemon_hedge.py` 의 재호가 2건."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from tests.features.spot.conftest import make_account
from tests.features.spot.helpers import ok_decision
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.accounts.settings import to_broker_account
from trader.features.spot import executor, reprice
from trader.features.spot import jobs as spot_jobs
from trader.features.spot import ledger as spot_ledger
from trader.features.spot import monitor as monitor_mod
from trader.features.spot.settings import RepriceCfg
from trader.kernel.broker.fake import FakeBroker, FakeMarket
from trader.kernel.broker.models import Cash
from trader.kernel.config import Secrets
from trader.kernel.execution import ledger as repo
from trader.kernel.ledger import control, db
from trader.kernel.session.run_store import read_checkpoint, run_dir

D = date(2026, 10, 23)
T0 = datetime(2026, 10, 23, 0, 40, tzinfo=UTC)  # 09:40 KST
CFG = RepriceCfg()


class Notes:
    def __init__(self):
        self.sent = []

    def send(self, text, *, escape=True):
        self.sent.append(text)
        return True


class _Port(AccountsPortImpl):
    """합성 계좌 + 테스트의 FakeBroker·시세를 돌려주는 계좌 포트(네트워크 없음)."""

    def __init__(self, accounts, brokers, market=None):
        super().__init__(accounts, Secrets())
        self._brokers, self._market = brokers, market

    def brokers(self):
        return self._brokers

    def market_client(self):
        return self._market


def _accounts(paper_mode="auto"):
    return [
        make_account("paper-main", "paper", paper_mode, markets=["KR"]),
        make_account("real-main", "real", "off", markets=["KR"]),
    ]


def _brokers(accounts):
    cash = {"KR": Cash(currency="KRW", available=Decimal("10000000"), total=Decimal("10000000"))}
    return {a.id: FakeBroker(to_broker_account(a), cash=cash) for a in accounts}


def _ctx(conn, settings, *, paper_mode="auto"):
    accounts = _accounts(paper_mode)
    return executor.exec_ctx_from(
        settings, conn, _Port(accounts, _brokers(accounts)), Notes(), lambda: T0, None
    )


def _order(conn, *, price="70000", status="accepted", filled=0, side="BUY", account="paper-main"):
    spot_ledger.ingest_checkpoint(
        conn, D, "KR", "005930", "30_decision", {"data": ok_decision(limit_price=float(price))}
    )
    did = conn.execute("SELECT id FROM decisions ORDER BY id DESC LIMIT 1").fetchone()["id"]
    oid = repo.insert_order(
        conn,
        decision_id=did,
        account_id=account,
        ticker="005930",
        market="KR",
        side=side,
        qty=10,
        order_type="LIMIT",
        price=Decimal(price),
        status=status,
        order_no="0001",
        message="",
        mode="auto",
        created_at="2026-10-23T00:05:00+00:00",
        org_no="00000",
    )
    if filled:
        repo.insert_fill(
            conn,
            order_id=oid,
            account_id=account,
            market="KR",
            ticker="005930",
            side=side,
            qty=filled,
            price=Decimal(price),
            filled_at=T0.isoformat(),
            order_no="0001",
        )
    return did, oid


def test_candidates_only_unfilled_limit_accepted_today(conn):
    _order(conn)
    _, o2 = _order(conn, filled=10)
    _, o3 = _order(conn, status="filled")
    rows = reprice.candidates(conn, "paper-main", "KR", D)
    assert [r["id"] for r in rows] == [1] and o2 != 1 and o3 != 1


def test_candidates_exclude_stop_loss_orders_by_origin(conn):
    """전체 검토 2차 2-O3: 최신 결정이 SELL/TRIM 인 날의 손절 지정가 SELL(US 손절)은 origin='stop'
    으로 가려 재호가 후보가 아니다 — 재제출이 "최신 결정 + 일반 게이트" 로 보호 매도를
    없애지 않게."""
    spot_ledger.ingest_checkpoint(
        conn, D, "KR", "005930", "30_decision", {"data": ok_decision(action="SELL")}
    )
    did = conn.execute("SELECT id FROM decisions ORDER BY id DESC LIMIT 1").fetchone()["id"]
    for origin, order_no in (("stop", "S1"), ("llm", "L1"), (None, "OLD")):
        repo.insert_order(
            conn,
            decision_id=did,
            account_id="paper-main",
            ticker="005930",
            market="KR",
            side="SELL",
            qty=10,
            order_type="LIMIT",
            price=Decimal("70000"),
            status="accepted",
            order_no=order_no,
            message="",
            mode="auto",
            created_at="2026-10-23T00:05:00+00:00",
            origin=origin,
        )
    rows = reprice.candidates(conn, "paper-main", "KR", D)
    assert [r["order_no"] for r in rows] == ["L1", "OLD"]


def test_too_far_rule():
    assert reprice.too_far("BUY", Decimal("100"), Decimal("101.5"), 0.01)
    assert not reprice.too_far("BUY", Decimal("100"), Decimal("100.9"), 0.01)
    assert reprice.too_far("SELL", Decimal("100"), Decimal("98.9"), 0.01)
    assert not reprice.too_far("SELL", Decimal("100"), Decimal("99.5"), 0.01)


def test_reprice_cancels_and_resubmits_at_quote_once_per_day(conn, settings):
    ctx = _ctx(conn, settings)
    did, oid = _order(conn, price="70000")
    out = reprice.run_once(
        ctx, market="KR", today=D, cfg=CFG, quote_fn=lambda t, e: Decimal("70300")
    )
    assert out["repriced"][0]["from"] == "70000" and out["repriced"][0]["to"] == "70300"
    assert out["repriced"][0]["approved"] is True and out["repriced"][0]["new_order"] == "accepted"
    broker = ctx.brokers["paper-main"]
    assert broker.cancelled == ["0001"] and len(broker.orders) == 1
    assert broker.orders[0].price == Decimal("70300") and broker.orders[0].order_type == "LIMIT"
    rows = conn.execute("SELECT id, status, price, decision_id FROM orders ORDER BY id").fetchall()
    assert rows[0]["status"] == "cancelled" and rows[1]["status"] == "accepted"
    assert rows[1]["decision_id"] == did and rows[1]["price"] == "70300"
    d = run_dir(D, "KR", "005930")
    assert read_checkpoint(d, "42_reprice_paper-main")["data"]["approved"] is True
    assert read_checkpoint(d, "52_order_reprice_paper-main")["data"]["status"] == "accepted"
    assert read_checkpoint(d, "50_order_paper-main") is None  # 오늘 분석의 50_ 는 건드리지 않는다
    assert any("[재호가]" in m and "70,000원 → 70,300원" in m for m in ctx.notifier.sent)
    # 운영일 1회
    assert (
        reprice.run_once(ctx, market="KR", today=D, cfg=CFG, quote_fn=lambda t, e: Decimal(1))[
            "skipped"
        ]
        == "오늘 이미 실행"
    )
    assert control.get_flag(conn, reprice.done_key("KR", D)) is True


def test_reprice_skips_when_chase_too_far_or_dry_run_or_off(conn, settings):
    ctx = _ctx(conn, settings)
    _order(conn, price="70000")
    out = reprice.run_once(
        ctx,
        market="KR",
        today=D,
        cfg=CFG,
        quote_fn=lambda t, e: Decimal("71000"),  # +1.4%
    )
    assert out["repriced"] == [] and "불리" in out["skipped"][0]["why"]
    assert ctx.brokers["paper-main"].cancelled == []
    assert any("[재호가 보류]" in m for m in ctx.notifier.sent)
    control.set_flag(conn, reprice.done_key("KR", D), None)
    assert reprice.run_once(
        ctx, market="KR", today=D, cfg=CFG, quote_fn=lambda t, e: Decimal(1), dry_run=True
    ) == {"skipped": "dry-run"}
    assert reprice.run_once(
        ctx, market="KR", today=D, cfg=RepriceCfg(enabled=False), quote_fn=lambda t, e: Decimal(1)
    ) == {"skipped": "reprice.enabled=false"}
    # off 계좌의 주문은 건너뛴다
    conn.execute("DELETE FROM orders")
    conn.commit()
    _order(conn, price="70000", account="real-main")
    out = reprice.run_once(
        ctx, market="KR", today=D, cfg=CFG, quote_fn=lambda t, e: Decimal("70100")
    )
    assert out["repriced"] == [] and ctx.brokers["real-main"].cancelled == []


def test_candidates_exclude_stop_loss_and_hedge_orders(conn):
    """전체 검토 O2: 손절 SELL(결정은 BUY)·헤지 결정의 주문은 재호가하지 않는다."""
    _, o_buy = _order(conn)  # BUY 결정 + BUY 주문 → 후보
    _, o_sl = _order(conn, side="SELL")  # BUY 결정에 붙은 SELL = 손절 → 제외
    conn.execute("UPDATE decisions SET source='hedge'")
    conn.commit()
    assert reprice.candidates(conn, "paper-main", "KR", D) == []
    conn.execute("UPDATE decisions SET source='llm'")
    conn.commit()
    assert [r["id"] for r in reprice.candidates(conn, "paper-main", "KR", D)] == [o_buy]
    assert o_sl != o_buy


def test_reprice_gate_sees_quote_change_pct_and_trade_block(conn, settings):
    """전체 검토 2차 2-O5: 재제출 게이트가 재호가 시세의 당일 등락률(급등 추격 금지)·종목상태
    (거래정지 등)를 본다 — quote_fn 이 `.price`·`.change_pct`·상태 코드를 가진 시세를 주면 price
    밖의 필드도 쓴다. Decimal 만 주는 호출자는 그대로."""
    from types import SimpleNamespace as NS

    ctx = _ctx(conn, settings)
    _order(conn, price="70000")
    out = reprice.run_once(
        ctx,
        market="KR",
        today=D,
        cfg=CFG,
        quote_fn=lambda t, e: NS(price=Decimal("70300"), change_pct=0.12),
    )
    r = out["repriced"][0]
    assert r["to"] == "70300" and r["approved"] is False
    rc = read_checkpoint(run_dir(D, "KR", "005930"), "42_reprice_paper-main")["data"]
    assert any("당일 등락률" in s for s in rc["reasons"])
    # 거래정지 종목 → 매수 거부
    control.set_flag(conn, reprice.done_key("KR", D), None)
    conn.execute("DELETE FROM orders")
    conn.commit()
    _order(conn, price="70000")
    out = reprice.run_once(
        ctx,
        market="KR",
        today=D,
        cfg=CFG,
        quote_fn=lambda t, e: NS(price=Decimal("70300"), change_pct=0.0, status_code="58"),
    )
    assert out["repriced"][0]["approved"] is False
    rc = read_checkpoint(run_dir(D, "KR", "005930"), "42_reprice_paper-main")["data"]
    assert any("거래정지" in s for s in rc["reasons"])


# --- 옛 tests/test_daemon_hedge.py: monitor 틱의 재호가 ---------------------------------


def _deps(settings, market, now):
    accounts = _accounts()
    port = _Port(accounts, _brokers(accounts), market)
    deps = SimpleNamespace(
        settings=settings,
        conn_factory=db.connect,
        state=None,
        now=lambda: now,
        notifier=Notes(),
        dry_run=False,
        ports={"accounts": port},
        queue=None,
    )
    return deps


def _ectx(deps, conn):
    return executor.exec_ctx_from(
        deps.settings, conn, deps.ports["accounts"], deps.notifier, deps.now, deps.state
    )


def test_monitor_body_runs_reprice_after_open_once(conn, settings, monkeypatch):
    """Phase 23 B: monitor 틱이 개장 after_min 뒤 재호가를 1회 부른다(그 전엔 대기)."""
    early = datetime(2026, 10, 23, 0, 10, tzinfo=UTC)  # 09:10 KST
    deps = _deps(settings, FakeMarket(), early)
    ectx = _ectx(deps, conn)
    assert spot_jobs._reprice_if_due(deps, ectx, "KR", D) == {"skipped": "개장 뒤 대기"}
    deps.now = lambda: datetime(2026, 10, 23, 0, 40, tzinfo=UTC)  # 09:40 KST
    out = spot_jobs._reprice_if_due(deps, ectx, "KR", D)
    assert out == {"repriced": [], "skipped": []}
    assert control.get_flag(conn, reprice.done_key("KR", D)) is True
    assert spot_jobs._reprice_if_due(deps, ectx, "KR", D) == {"skipped": "오늘 이미 실행"}
    deps.dry_run = True
    control.set_flag(conn, reprice.done_key("KR", D), None)
    assert spot_jobs._reprice_if_due(deps, ectx, "KR", D) == {"skipped": "비활성"}
    # monitor_body 가 monitor_once 앞에서 1회 부르고, 결과를 "reprice" 키로 싣는다
    monkeypatch.setattr(monitor_mod, "monitor_once", lambda ctx, **kw: {"checked": 0})
    deps.dry_run = False
    out = spot_jobs.monitor_body(deps, SimpleNamespace(market="KR"))
    assert out["checked"] == 0 and out["reprice"] == {"repriced": [], "skipped": []}
    out = spot_jobs.monitor_body(deps, SimpleNamespace(market="KR"))
    assert out == {"checked": 0}


def test_reprice_body_quote_fn_returns_full_quote(conn, settings, monkeypatch):
    """2-O5: 데몬의 재호가 quote_fn 은 가격만이 아니라 시세 객체(등락률·종목상태)를 넘긴다."""
    market = FakeMarket(prices={"005930": Decimal("70300")})
    deps = _deps(settings, market, datetime(2026, 10, 23, 0, 40, tzinfo=UTC))
    seen = {}

    def fake_run_once(ctx, *, quote_fn, **kw):
        seen["q"] = quote_fn("005930", None)
        return {"repriced": [], "skipped": []}

    monkeypatch.setattr(reprice, "run_once", fake_run_once)
    assert spot_jobs._reprice_if_due(deps, _ectx(deps, conn), "KR", D) == {
        "repriced": [],
        "skipped": [],
    }
    q = seen["q"]
    assert Decimal(str(q.price)) == Decimal("70300") and hasattr(q, "change_pct")
