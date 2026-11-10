"""spot 게이트의 `ports["regime"]` 배선: 국면·전략가 기울기·킬스위치·블랙아웃·excluded_tickers·
승인 재판정.

옛 `tests/orchestrator/test_hedge.py` 3건·`tests/risk/test_blackout.py` 1건 이식(치환:
`executor.regime_state` → `ports["regime"].state`, `Settings(hedge=…)` → 설정 `model_copy` 뒤 포트
재조립, 날짜는 spot conftest 의 D). 나머지는 M6 신규."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.features.spot.conftest import T0, D, ectx, make_account
from tests.features.spot.helpers import ok_decision
from trader.features.accounts import modes
from trader.features.accounts.settings import to_broker_account
from trader.features.regime import blackout, regime
from trader.features.regime import ports as regime_ports
from trader.features.regime.settings import HedgeCfg
from trader.features.spot import executor
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.excluded import excluded_tickers
from trader.features.spot.schemas import Decision
from trader.features.spot.settings import CashParkCfg
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash, Position
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import control

CFG = HedgeCfg(ma_days=20, momentum_days=5, exit_streak_days=1)


def _cash():
    return {
        "KR": Cash(currency="KRW", available=Decimal("10000000"), total=Decimal("10000000")),
        "US": Cash(currency="USD", available=Decimal("10000"), total=Decimal("10000")),
    }


def _accounts():
    return [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("real-main", "real", "off", markets=["KR"]),
    ]


def _with_hedge(ports, settings, hedge=CFG):
    """설정의 hedge 를 바꾸고 regime 포트를 다시 조립한다(옛 `ctx.settings = Settings(hedge=…)`)."""
    s = settings.model_copy(update={"hedge": hedge})
    ports["regime"] = regime_ports.build_port(SimpleNamespace(settings=s))
    return s


def _ctx(ports, settings, conn, *, positions=None, accounts=None):
    accounts = accounts or _accounts()
    brokers = {
        a.id: FakeBroker(to_broker_account(a), cash=_cash(), positions=list(positions or []))
        for a in accounts
    }
    return ectx(ports, settings, conn, accounts, brokers)


def _set_regime(conn, state, count=2):
    control.set_flag(
        conn,
        "regime:KR",
        {
            "state": state,
            "count": count,
            "signals": {"ma_below": True, "momentum_weak": count >= 2, "vol_high": count >= 3},
        },
    )


def _ingest(conn, dec):
    data = {"data": dec.model_dump()}
    spot_ledger.ingest_checkpoint(conn, D, "KR", dec.ticker, "30_decision", data)


def _paper(res):
    return next(r for r in res if r["account_id"] == "paper-main")


def test_regime_bear_blocks_normal_buy_and_hedge_positions_dont_count(
    project, conn, settings, ports
):
    hedge_pos = Position(
        ticker="114800",
        market="KR",
        qty=400,
        avg_price=Decimal("5000"),
        current_price=Decimal("5000"),
        eval_amount=Decimal("2000000"),
        pnl_pct=0.0,
    )
    s = _with_hedge(ports, settings)
    ctx = _ctx(ports, s, conn, positions=[hedge_pos])
    _set_regime(conn, "bear", 2)
    dec = Decision(**ok_decision())
    _ingest(conn, dec)
    res = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=False)
    paper = _paper(res)
    assert not paper["approved"] and any("bear" in s for s in paper["reasons"])
    assert "국면 bear" in paper["note"]
    # neutral 이면 한도 축소 사유와 함께 통과
    _set_regime(conn, "neutral", 1)
    res = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=True)
    paper = _paper(res)
    assert paper["approved"] and any("neutral" in s for s in paper["reasons"])


def test_regime_state_defaults_to_bull_when_disabled_or_missing(conn, settings, ports):
    _with_hedge(ports, settings)
    assert ports["regime"].state(conn, "KR") == "bull"
    regime.update(
        conn, "KR", closes=[100.0] * 30 + [95.0, 90.0, 85.0, 80.0, 75.0, 70.0], cfg=CFG, today=D
    )
    assert ports["regime"].state(conn, "KR") == "bear"
    _with_hedge(ports, settings, HedgeCfg(enabled=False))
    assert ports["regime"].state(conn, "KR") == "bull"


def test_agent_gate_opens_bear_buy_when_strategist_risk_on(project, conn, settings, ports):
    """Phase 22: hedge.agent_gate — 오늘 제안이 RISK_ON(확신 ≥ 0.7)이면 bear 에서도 신규 매수가
    10%·2종목 한도로 통과한다. 확신이 낮거나 agent_gate=false 면 기존대로 거부."""
    s = _with_hedge(ports, settings)
    ctx = _ctx(ports, s, conn)
    _set_regime(conn, "bear", 2)
    dec = Decision(**ok_decision(target_weight=0.2))
    _ingest(conn, dec)
    view = {
        "date": D.isoformat(),
        "stance": "RISK_ON",
        "confidence": 0.8,
        "hedge_weight": 0.0,
        "leveraged": False,
    }
    control.set_flag(conn, "market_view:KR", view)
    res = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=True)
    paper = _paper(res)
    assert paper["approved"] and paper["qty"] == 14  # 10% × 1천만 / 7만
    assert any("조건부 허용" in s for s in paper["reasons"])
    # 확신 부족 → 거부
    control.set_flag(conn, "market_view:KR", {**view, "confidence": 0.5})
    res = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=True)
    assert not _paper(res)["approved"]
    # agent_gate 끔 → 거부
    control.set_flag(conn, "market_view:KR", view)
    _with_hedge(ports, settings, CFG.model_copy(update={"agent_gate": False}))
    res = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=True)
    assert not _paper(res)["approved"]


def test_executor_blocks_buy_in_window_when_enabled(conn, project, settings, ports):
    blackout.store(conn, {"events": [{"ticker": "005930", "kind": "paidin", "date": "2026-10-12"}]})
    acct = make_account("paper-main", "paper", "auto")
    for enabled, blocked in ((False, False), (True, True)):
        eb = settings.event_blackout.model_copy(update={"enabled": enabled})
        s = settings.model_copy(update={"event_blackout": eb})
        ports["regime"] = regime_ports.build_port(SimpleNamespace(settings=s))
        ctx = _ctx(ports, s, conn, accounts=[acct])
        res = executor.execute(
            ctx,
            Decision(**ok_decision(action="BUY", target_weight=0.1, ticker="005930")),
            date=D,
            market="KR",
            price=Decimal("70000"),
            dry_run=True,
        )
        assert any("권리 일정" in r for r in res[0]["reasons"]) is blocked


def test_gate_defaults_to_bull_without_regime_port(project, conn, settings):
    """regime 포트가 없으면(옛 테스트·포트 미조립) 국면 bull·킬스위치·블랙아웃 없음으로 본다 —
    bear 플래그·켜진 킬스위치가 있어도 게이트는 그것을 모른다."""
    _set_regime(conn, "bear", 3)
    control.set_flag(conn, "killswitch", {"on": True, "reason": "합성", "accounts": None})
    ctx = _ctx({}, settings, conn)
    dec = Decision(**ok_decision())
    _ingest(conn, dec)
    paper = _paper(executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000")))
    assert paper["approved"] and paper["qty"] == 14
    assert not any("국면" in r or "킬스위치" in r for r in paper["reasons"])
    assert "국면" not in (paper["note"] or "")
    assert excluded_tickers(settings, None, "KR") == frozenset()


def test_killswitch_trip_cancels_open_buys_but_keeps_hedge_and_rejects_new_buy(
    project, conn, settings, ports
):
    """잔고를 볼 때 누적 손실이 한도를 넘으면 킬스위치가 켜지고(그 계좌만), 실제 실행(dry-run 아님)
    이면 그 계좌·시장의 당일 미체결 BUY 를 거두되 헤지 ETF 는 남긴다. 새 일반 BUY 는 거부."""
    ctx = _ctx(ports, settings, conn)
    paper_b = ctx.brokers["paper-main"]
    dec = Decision(**ok_decision())
    _ingest(conn, dec)
    executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=False)
    hedge_kw = {"ticker": "114800", "limit_price": 5000.0, "stop_loss": None, "take_profit": None}
    hedge = Decision(**ok_decision(**hedge_kw))
    _ingest(conn, hedge)
    executor.execute(
        ctx, hedge, date=D, market="KR", price=Decimal("5000"), dry_run=False, is_hedge=True
    )
    assert [o.ticker for o in paper_b.orders] == ["005930", "114800"]
    # 기준 NAV 를 두 배로 잡아 지금 NAV(1천만)가 −50% 로 보이게 한다(합성)
    control.set_flag(conn, "killswitch_base:paper-main:KR", {"nav": "20000000"})
    other = Decision(**ok_decision(ticker="000660", limit_price=150000.0))
    _ingest(conn, other)
    res = executor.execute(ctx, other, date=D, market="KR", price=Decimal("150000"), dry_run=False)
    paper = _paper(res)
    assert not paper["approved"]
    assert any(r.startswith("킬스위치(신규 매수 중단):") for r in paper["reasons"])
    assert paper_b.cancelled == ["FAKE-1"]  # 005930 만, 헤지 114800 은 그대로
    assert ports["regime"].killswitch_reason(conn, "paper-main")
    assert ports["regime"].killswitch_reason(conn, "real-main") is None
    # dry-run 재실행은 브로커 취소를 부르지 않는다
    executor.execute(ctx, other, date=D, market="KR", price=Decimal("150000"), dry_run=True)
    assert paper_b.cancelled == ["FAKE-1"]


def _pending_approval(ctx, conn, ticker="005930"):
    """approve 모드 계좌의 BUY 승인 요청 1건을 만든다(합성)."""
    modes.set_account_mode(conn, ctx.accounts[0], "approve")
    dec = Decision(**ok_decision(ticker=ticker))
    _ingest(conn, dec)
    res = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=False)
    aid = _paper(res)["approval_id"]
    assert aid
    return aid


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("killswitch", "킬스위치(신규 매수 중단): 합성 사유"),
        ("bear", "국면 bear: 신규 매수 중단"),
    ],
    ids=["killswitch", "bear"],
)
def test_approval_recheck_rejects_when_killswitch_or_bear(
    project, conn, settings, ports, setup, expected
):
    ctx = _ctx(ports, settings, conn)
    aid = _pending_approval(ctx, conn)
    if setup == "killswitch":
        control.set_flag(conn, "killswitch", {"on": True, "reason": "합성 사유", "accounts": None})
    else:
        _set_regime(conn, "bear", 2)
    ctx.now = lambda: T0 + timedelta(minutes=5)
    r = executor.approve(ctx, aid)
    assert r["ok"] is False and expected in r["error"]
    assert ctx.brokers["paper-main"].orders == []
    assert xledger.get_approval(conn, aid)["status"] == "expired"


def test_excluded_tickers_union_of_hedge_and_cash_park(settings, ports):
    port = ports["regime"]
    assert excluded_tickers(settings, port, "KR") == frozenset({"114800", "252670"})
    assert excluded_tickers(settings, port, "US") == frozenset({"SH", "SDS"})
    s = settings.model_copy(update={"cash_park": CashParkCfg(enabled=True, ticker="423160")})
    assert excluded_tickers(s, port, "KR") == frozenset({"114800", "252670", "423160"})
    assert excluded_tickers(s, port, "US") == frozenset({"SH", "SDS"})  # 현금 운용은 KR 만
    off = settings.model_copy(update={"cash_park": CashParkCfg(ticker="423160")})
    assert excluded_tickers(off, port, "KR") == frozenset({"114800", "252670"})
    assert excluded_tickers(s, None, "KR") == frozenset({"423160"})


def _manual(conn, mode):
    control.set_flag(conn, "risk_policy:KR", {"mode": mode, "date": D.isoformat(),
                     "expires_at": (T0 + timedelta(hours=14)).isoformat(), "reason": "수동 검토"})


@pytest.mark.parametrize("state", ["bull", "neutral", "bear"])
def test_manual_on_caps_all_regimes_without_replacing_ai(project, conn, settings, ports, state):
    s = _with_hedge(ports, settings)
    ctx = _ctx(ports, s, conn)
    _set_regime(conn, state)
    ai = {"date": D.isoformat(), "stance": "RISK_OFF", "confidence": 0.2}
    control.set_flag(conn, "market_view:KR", ai)
    _manual(conn, "RISK_ON")
    dec = Decision(**ok_decision(target_weight=0.25))
    res = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=True)
    assert _paper(res)["approved"] and _paper(res)["qty"] == 14
    assert control.get_flag(conn, "market_view:KR") == ai


def test_manual_off_blocks_add_and_approval_but_leaves_hedge(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn)
    aid = _pending_approval(ctx, conn)
    _manual(conn, "RISK_OFF")
    r = executor.approve(ctx, aid)
    assert not r["ok"] and "수동 RISK_OFF" in r["error"]
    assert ctx.brokers["paper-main"].orders == []
    dec = Decision(**ok_decision(ticker="000660", action="ADD"))
    res = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=True)
    assert not _paper(res)["approved"] and any("수동 RISK_OFF" in r for r in _paper(res)["reasons"])
    hedge = Decision(**ok_decision(ticker="114800", limit_price=5000.0, stop_loss=None,
                                  take_profit=None))
    res = executor.execute(ctx, hedge, date=D, market="KR", price=Decimal("5000"),
                           dry_run=True, is_hedge=True)
    assert _paper(res)["approved"]


def test_manual_on_approval_rechecks_reduced_position_limit(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn)
    modes.set_account_mode(conn, ctx.accounts[0], "approve")
    dec = Decision(**ok_decision(target_weight=0.2))
    _ingest(conn, dec)
    res = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=False)
    aid = _paper(res)["approval_id"]
    _manual(conn, "RISK_ON")
    r = executor.approve(ctx, aid)
    assert not r["ok"] and "한도" in r["error"]
    assert ctx.brokers["paper-main"].orders == []


def test_approval_recheck_counts_order_after_operating_day_changes(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn)
    aid = _pending_approval(ctx, conn)
    a = xledger.get_approval(conn, aid)
    ctx.now = lambda: T0 + timedelta(days=1)
    ctx.settings = ctx.settings.model_copy(update={"risk": ctx.settings.risk.model_copy(
        update={"max_position_pct": 0.05})})
    why = executor._approval_recheck(ctx)(a, a["order"])
    assert isinstance(why, str) and "한도" in why


def test_manual_on_keeps_pause_and_smaller_existing_limit(project, conn, settings, ports):
    risk = settings.risk.model_copy(update={"max_position_pct": 0.05, "max_positions": 1})
    s = _with_hedge(ports, settings.model_copy(update={"risk": risk}))
    ctx = _ctx(ports, s, conn)
    _set_regime(conn, "bear")
    _manual(conn, "RISK_ON")
    dec = Decision(**ok_decision(target_weight=0.25))
    got = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=True)
    assert _paper(got)["approved"] and _paper(got)["qty"] == 7
    control.set_flag(conn, "paused", True)
    got = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=False)
    assert _paper(got)["order"] is None and ctx.brokers["paper-main"].orders == []


def test_manual_on_expires_without_allowing_next_day_bear(project, conn, settings, ports):
    ctx = _ctx(ports, _with_hedge(ports, settings), conn)
    _set_regime(conn, "bear")
    _manual(conn, "RISK_ON")
    ctx.now = lambda: T0 + timedelta(days=1)
    dec = Decision(**ok_decision())
    got = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=True)
    assert not _paper(got)["approved"] and any("bear" in r for r in _paper(got)["reasons"])


def test_manual_on_respects_two_equity_position_cap(project, conn, settings, ports):
    positions = [Position(ticker=t, market="KR", qty=1, avg_price=Decimal("70000"),
                          current_price=Decimal("70000"), eval_amount=Decimal("70000"),
                          pnl_pct=0.0) for t in ("000660", "035420")]
    ctx = _ctx(ports, _with_hedge(ports, settings), conn, positions=positions)
    _manual(conn, "RISK_ON")
    dec = Decision(**ok_decision())
    got = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=True)
    assert not _paper(got)["approved"] and any("종목 수" in r for r in _paper(got)["reasons"])


@pytest.mark.parametrize("origin", ["park", "scalp"])
def test_manual_policy_does_not_remove_existing_other_lane_approval_checks(
    project, conn, settings, ports, origin
):
    ctx = _ctx(ports, settings, conn)
    aid = _pending_approval(ctx, conn)
    a = xledger.get_approval(conn, aid)
    _manual(conn, "RISK_ON")
    control.set_flag(conn, "killswitch", {"on": True, "reason": "기존 보호", "accounts": None})
    order = {**a["order"], "origin": origin}
    assert "킬스위치" in executor._approval_recheck(ctx)(a, order)


def _three_positions_ctx(ports, settings, conn):
    positions = [Position(ticker=t, market="KR", qty=1, avg_price=Decimal("70000"),
                          current_price=Decimal("70000"), eval_amount=Decimal("70000"),
                          pnl_pct=0.0) for t in ("005930", "000660", "035420")]
    s = _with_hedge(ports, settings.model_copy(update={
        "risk": settings.risk.model_copy(update={"max_positions": 2})}))
    ctx = _ctx(ports, s, conn, positions=positions)
    for broker in ctx.brokers.values():
        broker.cash["KR"] = Cash(currency="KRW", available=Decimal("9790000"),
                                  total=Decimal("9790000"))
    return ctx


def test_auto_add_to_existing_over_count_portfolio_keeps_approval_allowed(
    project, conn, settings, ports
):
    ctx = _three_positions_ctx(ports, settings, conn)
    modes.set_account_mode(conn, ctx.accounts[0], "approve")
    dec = Decision(**ok_decision(action="ADD", target_weight=0.08))
    _ingest(conn, dec)
    got = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=False)
    paper = _paper(got)
    assert paper["approved"] and paper["approval_id"]
    result = executor.approve(ctx, paper["approval_id"])
    assert result["ok"] and len(ctx.brokers["paper-main"].orders) == 1


def test_manual_on_existing_over_count_add_is_blocked_by_first_gate(
    project, conn, settings, ports
):
    ctx = _three_positions_ctx(ports, settings, conn)
    _manual(conn, "RISK_ON")
    dec = Decision(**ok_decision(action="ADD", target_weight=0.08))
    got = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=True)
    assert not _paper(got)["approved"]
    assert any("종목 수" in reason for reason in _paper(got)["reasons"])


def test_manual_on_existing_over_count_add_is_blocked_at_approval(
    project, conn, settings, ports
):
    ctx = _three_positions_ctx(ports, settings, conn)
    modes.set_account_mode(conn, ctx.accounts[0], "approve")
    dec = Decision(**ok_decision(action="ADD", target_weight=0.08))
    _ingest(conn, dec)
    got = executor.execute(ctx, dec, date=D, market="KR", price=Decimal("70000"), dry_run=False)
    paper = _paper(got)
    assert paper["approved"] and paper["approval_id"]
    _manual(conn, "RISK_ON")
    result = executor.approve(ctx, paper["approval_id"])
    assert not result["ok"] and "종목 수" in result["error"]
    assert ctx.brokers["paper-main"].orders == []
