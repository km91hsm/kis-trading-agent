"""spot executor 의 RL 사이저 섀도·적용(모의 계좌 한정) — `ports["evaluation"]` 경유.

옛 `tests/orchestrator/test_executor.py` L498-575(8건) 이식. 치환: `ectx.rl_sizer = …` →
evaluation 포트의 `rl_sizer` 를 monkeypatch."""

from decimal import Decimal

import pytest

from tests.features.spot.conftest import PRICE, D, ectx, make_account
from tests.features.spot.helpers import ok_decision
from trader.features.accounts import modes
from trader.features.accounts.settings import to_broker_account
from trader.features.evaluation.rl.policy import FixedPolicy
from trader.features.evaluation.rl.shadow import Sizer
from trader.features.ml.api import RlSizerCfg
from trader.features.spot import executor
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.schemas import Decision
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash


def _sizer(mode, weight=0.05):
    return Sizer(FixedPolicy(weight), "v1", RlSizerCfg(enabled=True, mode=mode))


def _broker(a):
    return FakeBroker(
        to_broker_account(a),
        cash={"KR": Cash(currency="KRW", available=Decimal("10000000"), total=Decimal("10000000"))},
    )


@pytest.fixture
def rl(project, conn, settings, ports, monkeypatch):
    """(ExecContext, 사이저 설정 함수). 계좌는 모의 auto + 실전 off."""
    accounts = [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("real-main", "real", "off", markets=["KR"]),
    ]
    ctx = ectx(ports, settings, conn, accounts, {a.id: _broker(a) for a in accounts})

    def set_sizer(sizer):
        monkeypatch.setattr(ports["evaluation"], "rl_sizer", lambda state: sizer)

    return ctx, set_sizer


def _run(ctx, decision=None, *, dry_run=False):
    dec = Decision(**(decision or ok_decision()))
    cp = {"data": dec.model_dump()}
    spot_ledger.ingest_checkpoint(ctx.conn, D, "KR", dec.ticker, "30_decision", cp)
    return executor.execute(ctx, dec, date=D, market="KR", price=PRICE, dry_run=dry_run)


def _shadow_rows(ctx, account_id=None):
    sql, args = "SELECT * FROM rl_shadow", ()
    if account_id:
        sql, args = sql + " WHERE account_id=?", (account_id,)
    return ctx.conn.execute(sql, args).fetchall()


def test_shadow_mode_records_but_does_not_change_qty(rl):
    ctx, set_sizer = rl
    set_sizer(_sizer("shadow"))
    by = {r["account_id"]: r for r in _run(ctx)}
    assert by["paper-main"]["qty"] == 14  # 변경 없음
    rows = _shadow_rows(ctx)
    assert len(rows) == 1 and rows[0]["account_id"] == "paper-main"
    assert rows[0]["proposed_weight"] == 0.05 and rows[0]["applied"] == 0


def test_apply_mode_changes_paper_qty_and_marks_applied(rl):
    ctx, set_sizer = rl
    set_sizer(_sizer("apply"))
    by = {r["account_id"]: r for r in _run(ctx)}
    assert by["paper-main"]["qty"] == 7  # 50만/지정가 71000
    assert by["paper-main"]["reasons"][0].startswith("RL 사이저 적용")
    assert _shadow_rows(ctx)[0]["applied"] == 1


def test_real_account_untouched_even_in_apply_mode(rl):
    ctx, set_sizer = rl
    for a in ctx.accounts:
        if a.env == "real":
            modes.set_account_mode(ctx.conn, a, "approve")
    set_sizer(_sizer("apply"))
    by = {r["account_id"]: r for r in _run(ctx)}
    assert not any(r.startswith("RL") for r in by["real-main"]["reasons"])
    assert _shadow_rows(ctx, "real-main") == []


def test_apply_mode_still_capped_by_risk_gate(rl):
    ctx, set_sizer = rl
    set_sizer(_sizer("apply", weight=0.90))
    by = {r["account_id"]: r for r in _run(ctx)}
    # 25% 상한(250만/71000 = 35주)보다 1회 손실 한도가 더 좁다: 1천만 × 2% ÷ (71000 − 65000) = 33주
    assert by["paper-main"]["qty"] == 33


def test_disabled_or_no_model_is_noop(rl):
    ctx, set_sizer = rl
    set_sizer(None)
    results = _run(ctx)
    assert _shadow_rows(ctx) == [] and results[0]["qty"] == 14


def test_hold_and_sell_skip_rl(rl):
    ctx, set_sizer = rl
    set_sizer(_sizer("apply"))
    _run(ctx, ok_decision(action="HOLD"))
    _run(ctx, ok_decision(action="SELL", target_weight=0.0))
    assert _shadow_rows(ctx) == []


def test_dry_run_does_not_record_shadow(rl):
    ctx, set_sizer = rl
    set_sizer(_sizer("shadow"))
    _run(ctx, dry_run=True)
    assert _shadow_rows(ctx) == []


def test_apply_mode_rejected_by_gate_not_marked_applied(rl):
    ctx, set_sizer = rl
    set_sizer(_sizer("apply", weight=0.05))
    # 기대수익 < 왕복비용×2 로 게이트가 거부 → 제안은 기록되지만 applied=0
    by = {r["account_id"]: r for r in _run(ctx, ok_decision(take_profit=71100.0))}
    assert by["paper-main"]["approved"] is False
    rows = _shadow_rows(ctx, "paper-main")
    assert len(rows) == 1 and rows[0]["applied"] == 0


def test_build_portfolio_updates_breaker_only_for_paper_and_record(rl, ports, monkeypatch):
    """서킷브레이커(옛 `_update_breaker`): 모의 계좌·스냅샷 기록일 때만 이 계좌 30일 NAV 로 갱신."""
    ctx, _ = rl
    calls: list = []

    def update_breaker(conn, *, market, nav_series):
        calls.append((market, [str(v) for _, v in nav_series]))

    monkeypatch.setattr(ports["evaluation"], "update_breaker", update_breaker)
    paper, real = ctx.accounts
    ev = ports["evaluation"]
    for acct, flag, record in ((paper, True, True), (real, False, True), (paper, True, False)):
        executor.build_portfolio(
            ctx.conn, ctx.brokers[acct.id], acct, "KR", D,
            record=record, evaluation=ev, breaker_account=flag,
        )  # fmt: skip
    assert calls == [("KR", ["10000000"])]
    calls.clear()
    _run(ctx, dry_run=True)  # execute 경로: 모의 계좌만 갱신
    assert calls == [("KR", ["10000000"])]


def _counting(rl, sizer, monkeypatch):
    """사이저 로드 횟수를 센다(ml.yaml 읽기·피클 로드는 RL 자격이 있을 때만)."""
    ctx, _ = rl
    calls: list = []

    def load(state):
        calls.append(state)
        return sizer

    monkeypatch.setattr(ctx.ports["evaluation"], "rl_sizer", load)
    return calls


def test_rl_sizer_loaded_only_when_eligible(rl, monkeypatch):
    ctx, _ = rl
    calls = _counting(rl, _sizer("shadow"), monkeypatch)
    _run(ctx, dry_run=True)
    _run(ctx, ok_decision(action="HOLD"))
    assert calls == []
    _run(ctx)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "kw",
    [{"is_stop_loss": True}, {"is_hedge": True}, {"is_park": True}, {"scalp_leg": "entry"}],
    ids=["stop", "hedge", "park", "scalp"],
)
def test_rl_skipped_for_stop_hedge_park_scalp_origins(rl, kw, monkeypatch):
    ctx, _ = rl
    calls = _counting(rl, _sizer("apply"), monkeypatch)
    dec = Decision(**ok_decision())
    spot_ledger.ingest_checkpoint(
        ctx.conn, D, "KR", dec.ticker, "30_decision", {"data": dec.model_dump()}
    )
    did = spot_ledger.decision_id(ctx.conn, D, "KR", dec.ticker)
    assert did is not None
    executor.execute(
        ctx, dec, date=D, market="KR", price=PRICE, dry_run=False, decision_id=did, **kw
    )
    assert calls == [] and _shadow_rows(ctx) == []
