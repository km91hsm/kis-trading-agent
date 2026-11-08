"""Phase 26 단계 4 — GitHub Codex PR #64 리뷰 반영(G4-1~6), 옛 `tests/fo/test_review_s4_github.py`
6건 이식. 치환: `daemon.queued_job_body(deps, req, …)()` → 커널 `queue_jobs.queued_job_body`(fo 큐
kind 표), `executor.fo_mode(conn, acct)` → `fo_mode(conn, acct, ports)`, 계좌 모드는 accounts
`modes.set_account_mode`."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from tests.features.fo._gate import quote
from tests.features.fo._support import (
    ACCT,
    TODAY,
    _held_position,
    account,
    at,
    balance,
    daemon_deps,
    decision,
    env,
    fill,
    holding,
    make_ctx,
)
from tests.features.fo._support import _engine_market as _market
from trader.features.accounts import modes
from trader.features.fo import engine, executor, store, triggers
from trader.kernel.broker.fake import FakeMarket
from trader.kernel.daemon import queue_jobs
from trader.kernel.execution import ledger
from trader.kernel.ledger import control


def _tick(ctx):
    return engine.tick(ctx, request_reeval=lambda c, p: None)


def test_accepted_entry_fill_synced_each_tick(conn):
    ctx = make_ctx(conn, market=_market(at(10, 30)), reeval={"periodic_max_per_day": 0})
    out = executor.run_leg(ctx, executor.account_of(ctx, ACCT), env(decision()))
    assert out[0]["status"] == "accepted"
    req = ctx.brokers[ACCT].orders[0]
    ctx.brokers[ACCT].fills[TODAY] = [fill("FAKE-1", filled=req.qty, amount=req.price * req.qty,
                                           remaining=0, side="BUY")]  # fmt: skip
    ctx.brokers[ACCT].balance = balance([holding(qty=req.qty)])
    _tick(ctx)
    pos = store.open_positions(conn, ACCT)[0]
    assert (pos.status, pos.qty_held) == ("held", req.qty)


def test_record_only_exit_keeps_pending_trigger(conn):
    now = at(10, 30)
    ctx = make_ctx(conn, market=_market(now, idx="344"), now=now, dry_run=True,
                   reeval={"periodic_max_per_day": 0})  # fmt: skip
    pos = _held_position(conn, ctx, qty=2)
    tid = triggers.record(conn, pos, triggers.Trigger("news", {}, None), now=now)
    out = _tick(ctx)
    assert out["accounts"][ACCT]["exits"][0]["status"] == "recorded"
    assert ledger.get_trigger(conn, tid)["handled_at"] is None


def test_paused_stop_exit_transmits_and_discards_trigger(conn):
    """일시정지 중 손절(stop_pt)은 위험 청산 — 주문을 내고 대기 재평가를 닫고, 하루 1회 알린다."""
    now = at(10, 30)
    ctx = make_ctx(conn, market=_market(now, idx="344"), now=now,
                   reeval={"periodic_max_per_day": 0})  # fmt: skip
    pos = _held_position(conn, ctx, qty=2)
    tid = triggers.record(conn, pos, triggers.Trigger("news", {}, None), now=now)
    control.set_flag(conn, "paused", True)
    out = _tick(ctx)
    ex = out["accounts"][ACCT]["exits"][0]
    assert (ex["intent"], ex["status"]) == ("STOP", "accepted")
    assert [o.side for o in ctx.brokers[ACCT].orders] == ["SELL"]
    assert ledger.get_trigger(conn, tid)["handled_at"] is not None
    assert sum("일시정지" in t for t in ctx.notifier.msgs) == 1
    assert store.get_position(conn, pos.id).status == "closing"


def test_skipped_queued_reevaluation_releases_trigger(conn):
    deps = daemon_deps(at(10, 30))
    ctx = make_ctx(conn)
    pos = _held_position(conn, ctx, qty=2)
    tid = triggers.record(conn, pos, triggers.Trigger("move", {}, Decimal("365")), now=at(10, 0))
    control.set_flag(conn, "paused", True)
    req = {"kind": "fo_reevaluate", "source": engine.REEVAL_SOURCE,
           "params": {"account_id": ACCT, "position_id": pos.id, "expected_version": pos.version,
                      "request_id": "r", "round": 1, "trigger_id": tid,
                      "date": TODAY.isoformat()}}  # fmt: skip
    out = queue_jobs.queued_job_body(deps, req, lambda s: None)
    assert "skipped" in out
    row = conn.execute("SELECT session_id, handled_at FROM triggers WHERE id = ?",
                       (tid,)).fetchone()  # fmt: skip
    assert row["handled_at"] and row["session_id"].startswith("보류")


def test_real_fo_account_never_orders_from_monitor(conn):
    real = account(env="real", mode="auto")
    now = at(10, 30)
    ctx = make_ctx(conn, acct=real, market=_market(now, idx="344"), now=now,
                   reeval={"periodic_max_per_day": 0})  # fmt: skip
    with store.tx(conn):
        pid = store.insert_position(conn, account_id=real.id, code="105W11", kind="FUTURES",
                                    contract_side="LONG", direction="LONG", vehicle="FUTURES",
                                    status="held", qty_target=2, qty_held=2,
                                    stop_pt=Decimal("345"))  # fmt: skip
    modes.set_account_mode(conn, real, "auto", confirm="LIVE")
    ctx.brokers[real.id].balance = balance([holding(qty=2)])
    _tick(ctx)
    assert ctx.brokers[real.id].orders == [] and ctx.brokers[real.id].cancelled == []
    assert store.get_position(conn, pid).status == "held"
    assert executor.fo_mode(conn, real, ctx.ports)[0] == "off"


def test_stale_quote_does_not_trigger_price_stop(conn):
    now = at(10, 30)
    old = now - timedelta(hours=1)
    market = FakeMarket(fo_quotes={"105W11": quote(price="340", quote_asof=old)},
                        kospi200=(Decimal("350"), now))  # fmt: skip
    ctx = make_ctx(conn, market=market, now=now, reeval={"periodic_max_per_day": 0})
    _held_position(conn, ctx, qty=2, stop_fut=Decimal("345"))
    out = _tick(ctx)
    assert out["accounts"][ACCT]["exits"] == [] and ctx.brokers[ACCT].orders == []


def test_decision_ref_prefers_latest_date_over_round(conn):
    ctx = make_ctx(conn)
    pos = _held_position(conn, ctx, qty=2)
    for d, rnd, idx in (("2026-10-26", 5, "340"), ("2026-10-27", 1, "360")):
        store.record_decision(conn, date=d, account_id=ACCT, position_id=pos.id, round=rnd,
                              request_id=f"r{rnd}", intent="HOLD", direction="LONG",
                              vehicle="FUTURES", target_leverage=0.3, confidence=0.6,
                              index_at_decision=idx, quote_asof=None,
                              data='{"decision": {}}')  # fmt: skip
    conn.commit()
    assert engine._decision_ref(conn, pos)["ref_index"] == Decimal("360")
