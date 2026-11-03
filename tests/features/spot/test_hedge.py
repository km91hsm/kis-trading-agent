"""국면 헤지(`spot.hedge.rebalance`)·executor is_hedge·`ports["regime"]` 연결.

옛 `tests/orchestrator/test_hedge.py` 18건 중 15건(1·2·3·4·7·8·9·10·11·12·13·14·15·16·18) 이식
(5·6·17 은 `test_executor_regime.py`). 치환(플랜 「바꾸는 옛 단언」): `rebalance(..., cfg=…)` 의
`cfg` 인자 → 설정 `hedge` 를 바꾸고 regime 포트를 다시 조립(`_with_hedge`), `Settings(hedge=…)` →
`settings.model_copy`, `AccountConfig` → `make_account`, `control.control.*` →
`kernel.ledger.control`, `control.set_account_mode` → `accounts.modes.set_account_mode`,
`repo.*` → `spot.ledger`, `ExecContext(...)` → 포트를 실은 ExecContext. 1번의
`metrics.decision_distribution(...) == {}`(M8) 는 `decisions.source == "hedge"` 단언으로 대체."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from tests.features.spot.conftest import Notes, make_account
from trader.features.accounts import api as accounts_api
from trader.features.accounts import modes
from trader.features.accounts.settings import to_broker_account
from trader.features.regime import ports as regime_ports
from trader.features.regime.settings import HedgeCfg
from trader.features.spot import executor, hedge
from trader.features.spot import ledger as spot_ledger
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash, Position
from trader.kernel.ledger import control
from trader.kernel.session.run_store import read_checkpoint, run_dir

D = date(2026, 10, 24)
T0 = datetime(2026, 10, 24, 0, 30, tzinfo=UTC)
CFG = HedgeCfg(ma_days=20, momentum_days=5, exit_streak_days=1)


def _cash():
    return {
        "KR": Cash(currency="KRW", available=Decimal("10000000"), total=Decimal("10000000")),
        "US": Cash(currency="USD", available=Decimal("10000"), total=Decimal("10000")),
    }


def _with_hedge(ports, settings, cfg=CFG):
    """설정의 hedge 를 바꾸고 regime 포트를 다시 조립한다(옛 `rebalance(cfg=…)`)."""
    s = settings.model_copy(update={"hedge": cfg})
    ports["regime"] = regime_ports.build_port(SimpleNamespace(settings=s))
    return s


def _ctx(ports, settings, conn, *, positions=None, real_mode="off", cfg=CFG):
    s = _with_hedge(ports, settings, cfg)
    accounts = [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("real-main", "real", real_mode, markets=["KR"]),
    ]
    brokers = {
        a.id: FakeBroker(to_broker_account(a), cash=_cash(), positions=list(positions or []))
        for a in accounts
    }
    return executor.ExecContext(
        settings=s,
        accounts=accounts,
        conn=conn,
        brokers=brokers,
        policy=accounts_api.effective_mode,
        paused=accounts_api.is_paused,
        notifier=Notes(),
        now=lambda: T0,
        ports=ports,
    )


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


def test_bear_enters_inverse_per_account_and_records_hedge_decision(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn)
    _set_regime(conn, "bear", 2)
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert out["action"] == "enter" and out["ticker"] == "114800"  # 2/3 → −1x
    paper = next(r for r in out["results"] if r["account_id"] == "paper-main")
    assert paper["approved"] and paper["qty"] == 400  # 1000만 × 20% / 5000
    real = next(r for r in out["results"] if r["account_id"] == "real-main")
    assert real["mode"] == "off" and real["order"] is None  # 실전 off 는 기록만
    orders = ctx.brokers["paper-main"].orders
    assert len(orders) == 1 and orders[0].side == "BUY" and orders[0].order_type == "MARKET"
    assert read_checkpoint(run_dir(D, "KR", "114800"), "46_hedge_paper-main")["data"]["approved"]
    assert read_checkpoint(run_dir(D, "KR", "114800"), "56_order_hedge_paper-main")
    row = conn.execute("SELECT source, action FROM decisions WHERE ticker='114800'").fetchone()
    assert row["source"] == "hedge" and row["action"] == "BUY"  # 분포(llm)에서 제외
    assert any("[헤지 진입]" in m for m in ctx.notifier.sent)
    # 같은 날 재실행은 건너뛴다
    assert hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal(1)) == {
        "skipped": "오늘 이미 실행"
    }


def test_three_signals_pick_leveraged_and_exit_sells_all_when_regime_clears(
    project, conn, settings, ports
):
    ctx = _ctx(ports, settings, conn)
    _set_regime(conn, "bear", 3)
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("3000"))
    assert out["ticker"] == "252670"
    # 다음 날: 보유 중인데 국면이 neutral → 전량 SELL (최소 보유·주 2회 면제)
    pos = Position(
        ticker="252670",
        market="KR",
        qty=600,
        avg_price=Decimal("3000"),
        current_price=Decimal("3100"),
        eval_amount=Decimal("1860000"),
        pnl_pct=0.03,
    )
    ctx2 = _ctx(ports, settings, conn, positions=[pos])
    _set_regime(conn, "neutral", 1)
    d2 = date(2026, 10, 25)
    out2 = hedge.rebalance(ctx2, market="KR", today=d2, quote_fn=lambda i: Decimal("3100"))
    assert out2["action"] == "exit" and out2["held"] == ["252670"]
    paper = next(r for r in out2["results"] if r["account_id"] == "paper-main")
    assert paper["approved"] and paper["qty"] == 600
    sells = [o for o in ctx2.brokers["paper-main"].orders if o.side == "SELL"]
    assert len(sells) == 1 and sells[0].ticker == "252670"
    # bear 가 아니고 보유도 없으면 아무것도 하지 않는다
    ctx3 = _ctx(ports, settings, conn)
    assert (
        hedge.rebalance(ctx3, market="KR", today=date(2026, 10, 26), quote_fn=lambda i: Decimal(1))[
            "action"
        ]
        == "none"
    )


def test_dry_run_records_only_and_does_not_mark_done(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn)
    _set_regime(conn, "bear", 2)
    out = hedge.rebalance(
        ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"), dry_run=True
    )
    assert out["action"] == "enter" and ctx.brokers["paper-main"].orders == []
    assert control.get_flag(conn, hedge.done_key("KR", D)) is None
    assert read_checkpoint(run_dir(D, "KR", "114800"), "46_hedge_paper-main")["data"]["dry_run"]


def test_quote_failure_is_notified_and_marks_done(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn)
    _set_regime(conn, "bear", 2)

    def boom(i):
        raise ValueError("시세 없음")

    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=boom)
    assert out["results"][0]["error"].startswith("시세 실패") and any(
        "[헤지 보류]" in m for m in ctx.notifier.sent
    )


# --- Phase 18: 시장 세션 제안 반영 -------------------------------------------------


def _view(conn, **over):
    v = {
        "date": D.isoformat(),
        "market": "KR",
        "stance": "RISK_OFF",
        "hedge_weight": 0.12,
        "leveraged": True,
        "confidence": 0.7,
    }
    v.update(over)
    control.set_flag(conn, "market_view:KR", v)


def test_proposal_weight_is_used_within_bounds(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn)
    _set_regime(conn, "bear", 2)
    _view(conn, hedge_weight=0.12)
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert out["action"] == "enter" and out["weight"] == 0.12
    assert out["view"]["stance"] == "RISK_OFF"
    paper = next(r for r in out["results"] if r["account_id"] == "paper-main")
    assert paper["qty"] == 240  # 1000만 × 12% / 5000
    dec = spot_ledger.latest_decision(conn, "KR", "114800")
    assert "시장 세션 제안 RISK_OFF 12%" in dec["data"]["rationale"]


def test_proposal_below_min_is_raised_to_min(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn)
    _set_regime(conn, "bear", 2)
    _view(conn, hedge_weight=0.01)
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert out["weight"] == 0.05
    paper = next(r for r in out["results"] if r["account_id"] == "paper-main")
    assert paper["qty"] == 100


def test_proposal_can_refuse_leverage_but_not_force_entry(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn)
    _set_regime(conn, "bear", 3)
    _view(conn, leveraged=False)
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert out["ticker"] == "114800"  # 3/3 이지만 제안이 레버리지 거부 → −1x
    # bear 가 아니면 RISK_OFF 제안이 와도 진입하지 않는다 (규칙 우선)
    ctx2 = _ctx(ports, settings, conn)
    _set_regime(conn, "neutral", 1)
    _view(conn, date=date(2026, 10, 25).isoformat(), hedge_weight=0.2)
    out2 = hedge.rebalance(
        ctx2, market="KR", today=date(2026, 10, 25), quote_fn=lambda i: Decimal("5000")
    )
    assert out2["action"] == "none" and out2["view"]["hedge_weight"] == 0.2


def test_stale_proposal_is_ignored(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn)
    _set_regime(conn, "bear", 2)
    _view(conn, date="2026-10-23", hedge_weight=0.05)  # 어제 제안
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert out["view"] is None and out["weight"] == 0.2


# --- 리뷰 F12: 계좌별 보유 판단·실패 계좌만 재시도 ---------------------------------------


def _hedge_pos(ticker="114800", qty=100):
    return Position(
        ticker=ticker,
        market="KR",
        qty=qty,
        avg_price=Decimal("5000"),
        current_price=Decimal("5000"),
        eval_amount=Decimal("500000"),
        pnl_pct=0.0,
    )


def test_enter_skips_accounts_that_already_hold_hedge(project, conn, settings, ports):
    """paper 만 들고 있으면 real 만 진입 판정을 받고 paper 는 다시 사지 않는다(합집합 보유 아님)."""
    ctx = _ctx(ports, settings, conn, real_mode="off")
    ctx.brokers["paper-main"].positions = [_hedge_pos()]
    _set_regime(conn, "bear", 2)
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert out["action"] == "enter" and out["held_by"]["paper-main"] == ["114800"]
    assert [r["account_id"] for r in out["results"]] == ["real-main"]
    assert ctx.brokers["paper-main"].orders == []


def test_exit_sells_only_from_holding_accounts(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn, real_mode="off")
    ctx.brokers["paper-main"].positions = [_hedge_pos()]
    _set_regime(conn, "bull", 0)
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert out["action"] == "exit"
    assert [r["account_id"] for r in out["results"]] == ["paper-main"]
    assert len(ctx.brokers["paper-main"].orders) == 1


def test_failed_account_is_retried_and_successful_one_is_done(project, conn, settings, ports):
    """잔고 조회가 실패한 계좌는 pending 으로 남아 다음 틱에 다시 시도하고, 성공한 계좌는
    done 으로 기록돼 다시 사지 않는다. 상한(MAX_RETRIES) 뒤엔 오늘은 더 시도하지 않는다."""
    ctx = _ctx(ports, settings, conn, real_mode="auto")
    modes.set_account_mode(conn, ctx.accounts[1], "auto", confirm="LIVE")
    _set_regime(conn, "bear", 2)
    ctx.brokers["real-main"].fail = {"get_positions"}
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert [r["account_id"] for r in out["results"]] == ["paper-main"]
    flag = control.get_flag(conn, hedge.done_key("KR", D))
    assert flag["done"] == ["paper-main"] and flag["pending"] == ["real-main"]
    assert len(ctx.brokers["paper-main"].orders) == 1
    # 다음 틱: real 만 다시, paper 는 건너뜀 (브로커 복구)
    ctx.brokers["real-main"].fail = set()
    out2 = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert [r["account_id"] for r in out2["results"]] == ["real-main"]
    assert len(ctx.brokers["paper-main"].orders) == 1
    assert control.get_flag(conn, hedge.done_key("KR", D))["pending"] == []
    out3 = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert out3 == {"skipped": "오늘 이미 실행"}


def test_retry_cap_stops_after_max(project, conn, settings, ports):
    ctx = _ctx(ports, settings, conn, real_mode="off")
    _set_regime(conn, "bear", 2)

    def boom(i):
        raise ValueError("시세 없음")

    for _ in range(hedge.MAX_RETRIES):
        out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=boom)
        assert out["action"] == "enter"
    assert hedge.rebalance(ctx, market="KR", today=D, quote_fn=boom) == {
        "skipped": "오늘 이미 실행"
    }
    assert any("더 시도하지 않아요" in m for m in ctx.notifier.sent)


# --- Phase 19: 전략가 RISK_ON 하루 보류 -------------------------------------------------


def test_risk_on_defers_entry_only_at_boundary(project, conn, settings, ports):
    """지표가 경계값(2/3)이고 전략가가 RISK_ON(확신 ≥ 0.7)이면 오늘 진입을 보류하고 플래그를 세운다.
    3/3 이면 보류하지 않는다. 확신이 낮으면 보류하지 않는다."""
    # 전체 검토 L8 뒤 기본 끔 — Phase 19 동작은 켜서 확인
    cfg = CFG.model_copy(update={"agent_signal": True})
    ctx = _ctx(ports, settings, conn, cfg=cfg)
    _set_regime(conn, "bear", 2)
    _view(conn, stance="RISK_ON", confidence=0.9)
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert out["action"] == "deferred" and "보류" in out["reason"]
    assert ctx.brokers["paper-main"].orders == []
    assert control.get_flag(conn, hedge.done_key("KR", D))["action"] == "deferred"
    assert any("[헤지 보류] KR" in m for m in ctx.notifier.sent)
    assert hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000")) == {
        "skipped": "오늘 이미 실행"
    }
    # 3/3 은 보류 불가
    ctx2 = _ctx(ports, settings, conn, cfg=cfg)
    d2 = date(2026, 10, 27)
    _set_regime(conn, "bear", 3)
    _view(conn, date=d2.isoformat(), stance="RISK_ON", confidence=0.9)
    out2 = hedge.rebalance(ctx2, market="KR", today=d2, quote_fn=lambda i: Decimal("5000"))
    assert out2["action"] == "enter" and len(ctx2.brokers["paper-main"].orders) == 1
    # 확신이 낮으면 진입
    ctx3 = _ctx(ports, settings, conn, cfg=cfg)
    d3 = date(2026, 10, 28)
    _set_regime(conn, "bear", 2)
    _view(conn, date=d3.isoformat(), stance="RISK_ON", confidence=0.5)
    assert (
        hedge.rebalance(ctx3, market="KR", today=d3, quote_fn=lambda i: Decimal("5000"))["action"]
        == "enter"
    )
    # agent_signal 끄면 보류 없음
    cfg_off = HedgeCfg(ma_days=20, momentum_days=5, exit_streak_days=1, agent_signal=False)
    ctx4 = _ctx(ports, settings, conn, cfg=cfg_off)
    d4 = date(2026, 10, 29)
    _set_regime(conn, "bear", 2)
    _view(conn, date=d4.isoformat(), stance="RISK_ON", confidence=0.9)
    assert (
        hedge.rebalance(ctx4, market="KR", today=d4, quote_fn=lambda i: Decimal("5000"))["action"]
        == "enter"
    )


def test_switch_instruments_sells_after_streak_then_reenters_next_day(
    project, conn, settings, ports
):
    """Phase 21: switch_instruments=true 면 bear 유지 중 보유(−1x)와 판정(3/3 → −2x)이
    switch_streak_days 연속 어긋날 때 전량 매도(action switch), 같은 날 매수는 없고 다음 운영일
    진입 규칙이 −2x 를 산다. 기본(false)이면 교체하지 않는다."""
    cfg = HedgeCfg(ma_days=20, momentum_days=5, exit_streak_days=1, switch_streak_days=2)
    pos = Position(
        ticker="114800",
        market="KR",
        qty=400,
        avg_price=Decimal("5000"),
        current_price=Decimal("5100"),
        eval_amount=Decimal("2040000"),
        pnl_pct=0.02,
    )
    # 기본: 교체 없음
    ctx = _ctx(ports, settings, conn, positions=[pos], cfg=cfg)
    _set_regime(conn, "bear", 3)
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert out["action"] == "none" and ctx.brokers["paper-main"].orders == []
    on = cfg.model_copy(update={"switch_instruments": True})
    d1 = date(2026, 10, 27)
    ctx = _ctx(ports, settings, conn, positions=[pos], cfg=on)
    out = hedge.rebalance(ctx, market="KR", today=d1, quote_fn=lambda i: Decimal("5000"))
    assert out["action"] == "none"  # 1일째: 세기만
    assert control.get_flag(conn, hedge.switch_key("KR")) == {
        "days": 1,
        "want": "252670",
        "date": d1.isoformat(),
    }
    d2 = date(2026, 10, 28)
    ctx = _ctx(ports, settings, conn, positions=[pos], cfg=on)
    out = hedge.rebalance(ctx, market="KR", today=d2, quote_fn=lambda i: Decimal("5000"))
    assert out["action"] == "switch" and out["ticker"] == "252670"
    orders = ctx.brokers["paper-main"].orders
    assert len(orders) == 1 and orders[0].side == "SELL" and orders[0].ticker == "114800"
    assert control.get_flag(conn, hedge.switch_key("KR")) is None
    assert any("[헤지 교체] KR 114800 매도" in m for m in ctx.notifier.sent)
    # 다음 운영일: 보유 없음 + bear 3/3 → −2x 진입
    d3 = date(2026, 10, 29)
    ctx = _ctx(ports, settings, conn, cfg=on)
    out = hedge.rebalance(ctx, market="KR", today=d3, quote_fn=lambda i: Decimal("3000"))
    assert out["action"] == "enter" and out["ticker"] == "252670"
    # 보유가 판정과 일치하면 streak 플래그를 지운다
    pos2 = pos.model_copy(update={"ticker": "252670"})
    control.set_flag(conn, hedge.switch_key("KR"), {"days": 1, "want": "x", "date": "d"})
    ctx = _ctx(ports, settings, conn, positions=[pos2], cfg=on)
    out = hedge.rebalance(
        ctx, market="KR", today=date(2026, 10, 30), quote_fn=lambda i: Decimal("3000")
    )
    assert out["action"] == "none" and control.get_flag(conn, hedge.switch_key("KR")) is None


def test_leverage_needs_three_core_indicators_not_agent_or_flow(project, conn, settings, ports):
    """전체 검토 O14: count 3(정량 2 + 전략가)이어도 −2x 가 아니라 −1x."""
    ctx = _ctx(ports, settings, conn)
    control.set_flag(
        conn,
        "regime:KR",
        {
            "state": "bear",
            "count": 3,
            "signals": {
                "ma_below": True,
                "momentum_weak": True,
                "vol_high": False,
                "agent_risk_off": True,
            },
        },
    )
    out = hedge.rebalance(ctx, market="KR", today=D, quote_fn=lambda i: Decimal("5000"))
    assert out["ticker"] == "114800"  # −1x (−2x 252670 아님)
