"""데몬·큐 ↔ 파생(옛 `tests/test_daemon_fo.py` 재작성 + 신규). 옛 `build_jobs(fo=)`·`_auto_gate`·
`_fo_monitor_job` 은 없다 — fo 가 `JobSpec` 3개·`QueueKind` 3개를 선언하고(A5) 본문이 계좌·주말·
휴장·일시정지·파생 시계를 직접 거른다. 텔레그램 `/fo`·STATUS 줄 건(옛 4건)은 Task 8.

Review Focus 3(`test_paused_engine_consumes_nothing`)·4(`test_pending_entry_runs_once_in_window`)·
5(`test_settle_requires_fills_ok_and_not_dry_run`)도 여기 둔다."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.features.fo._support import (
    ACCT,
    TODAY,
    _held_position,
    _pending_flag,
    at,
    balance,
    daemon_deps,
    decision,
    env,
    holding,
    make_ctx,
)
from tests.features.fo._support import _engine_market as _market
from trader.features.fo import engine, jobs, reeval, store, tools, triggers
from trader.features.fo import session as fo_session
from trader.features.fo.feature import FEATURE
from trader.kernel.config import KST
from trader.kernel.daemon import jobs as daemon_jobs
from trader.kernel.daemon import queue as jobq
from trader.kernel.ledger import control
from trader.kernel.session.runner import PipelineError

SAT = datetime(2026, 10, 31, 1, 30, tzinfo=UTC)  # 토요일 10:30 KST


def _job(job_id):
    return SimpleNamespace(id=job_id, market=None)


# --- 선언 -----------------------------------------------------------------------------------


def test_feature_declares_three_jobs_and_four_queue_kinds():
    specs = {j.id: j for j in FEATURE.jobs}
    assert list(specs) == ["kr_fo_session", "kr_fo_monitor", "kr_fo_reconcile"]
    s, m, r = specs["kr_fo_session"], specs["kr_fo_monitor"], specs["kr_fo_reconcile"]
    assert (s.kind, s.trigger, s.schedule_key, s.gate, s.heavy, s.days, s.catch_up) == (
        "fo_session", "cron", "fo.kr.session", "none", True, "mon-fri", "notify")
    assert (m.kind, m.trigger, m.schedule_key, m.interval_s, m.gate, m.heavy, m.start_soon_s) == (
        "fo_monitor", "interval", "fo.kr.monitor.interval_s", 60, "none", False, 30)
    assert (r.kind, r.trigger, r.schedule_key, r.gate, r.misfire, r.catch_up, r.days) == (
        "fo_reconcile", "cron", "reconcile.kr", "none", "idempotent", "queue", "mon-fri")
    assert not any(j.per_market for j in FEATURE.jobs)
    assert (s.body, m.body, r.body) == (jobs.session_job, jobs.monitor_job, jobs.reconcile_job)
    kinds = {q.kind: q for q in FEATURE.queue_kinds}
    assert list(kinds) == ["fo_session", "fo_reevaluate", "fo_close", "fo_reconcile"]
    got = {k: (q.params_model, q.label, q.lane, q.priority, q.trading, q.restart_requeue, q.body)
           for k, q in kinds.items()}  # fmt: skip
    assert got == {
        "fo_session": (jobs.EmptyParams, "파생 세션", "slow", 0, True, False, jobs.session_request),
        "fo_reevaluate": (jobs.FoReevaluateParams, "파생 재평가", "reeval", 10, True, False,
                          jobs.reevaluate_request),
        "fo_close": (jobs.FoCloseParams, "파생 청산", "fast", 10, True, False,
                     jobs.close_request),
        "fo_reconcile": (jobs.EmptyParams, "파생 대사", "fast", 0, False, True,
                         jobs.reconcile_request),
    }  # fmt: skip


def test_queue_params_models_forbid_extra_and_validate():
    ok = {"account_id": ACCT, "position_id": 1, "expected_version": 0, "request_id": "abc",
          "round": 1, "trigger_id": 1, "date": "2026-10-27"}  # fmt: skip
    kinds = jobq.kinds_of([FEATURE])
    assert jobq.validate(kinds, "fo_reevaluate", ok) == ok
    for bad in ({**ok, "extra": 1}, {**ok, "round": 100}, {**ok, "date": "20261027"},
                {**ok, "account_id": "a b"}, {**ok, "request_id": "a/b"}):  # fmt: skip
        with pytest.raises(ValueError):
            jobq.validate(kinds, "fo_reevaluate", bad)
    assert jobq.validate(kinds, "fo_close", {"account_id": ACCT, "position_id": 3}) == {
        "account_id": ACCT, "position_id": 3}
    with pytest.raises(ValueError):
        jobq.validate(kinds, "fo_close", {"account_id": ACCT, "position_id": 0})
    with pytest.raises(ValueError):
        jobq.validate(kinds, "fo_session", {"x": 1})


def test_queue_fo_reevaluate_priority(conn):
    """옛 `test_queue_fo_reevaluate_fast_lane_priority`: 재평가 레인·우선순위 10."""
    params = {"account_id": ACCT, "position_id": 1, "expected_version": 0, "request_id": "abc",
              "round": 1, "trigger_id": 1, "date": "2026-10-27"}  # fmt: skip
    rid = jobq.enqueue(conn, jobq.kinds_of([FEATURE]), "fo_reevaluate", params,
                       source=engine.REEVAL_SOURCE, now=at(10, 0))  # fmt: skip
    row = conn.execute("SELECT priority FROM job_requests WHERE id = ?", (rid,)).fetchone()
    assert row["priority"] == 10


def test_daemon_builds_fo_jobs_from_schedule():
    deps = daemon_deps(at(10, 0))
    built = {j.id: j for j in daemon_jobs.build(
        [FEATURE], schedule=deps.schedule, settings=deps.settings, markets=["KR"], ctx=deps)}
    sess = built["kr_fo_session"]
    assert sess.trigger_kwargs == {"hour": 8, "minute": 20, "timezone": KST,
                                   "day_of_week": "mon-fri"}  # fmt: skip
    assert built["kr_fo_monitor"].interval_s == 60 and built["kr_fo_monitor"].market is None
    rec = built["kr_fo_reconcile"]
    assert rec.trigger_kwargs["day_of_week"] == "mon-fri" and "hour" in rec.trigger_kwargs


# --- 모니터 잡 -------------------------------------------------------------------------------


def test_monitor_job_skips_without_accounts_or_outside_window(conn, monkeypatch):
    deps = daemon_deps(at(10, 0), accounts=[])
    assert jobs.monitor_job(deps, _job("kr_fo_monitor")) == {"skipped": "파생 계좌 없음"}
    for now in (at(8, 44), at(15, 46), at(16, 0)):
        assert jobs.monitor_job(daemon_deps(now), _job("kr_fo_monitor")) == {
            "skipped": "파생 창 밖"}, now
    assert "skipped" in jobs.monitor_job(daemon_deps(SAT), _job("kr_fo_monitor"))
    assert jobs.monitor_job(daemon_deps(at(10, 0), holidays={TODAY}),
                            _job("kr_fo_monitor")) == {"skipped": "휴장일"}  # fmt: skip
    seen: dict = {}

    def spy_tick(ctx, **kw):
        seen["accounts"] = [a.id for a in ctx.accounts]
        seen["in_tx"] = ctx.conn.in_transaction
        return {"accounts": {ACCT: {"exits": [1], "maintain": [], "triggers": [], "entries": [],
                                    "recalled": [], "halted": False, "snapshot": True}}}

    monkeypatch.setattr(engine, "tick", spy_tick)
    # 15:40 — 현물 장은 닫혔지만 파생 창(08:45~15:45) 안이면 돈다
    out = jobs.monitor_job(daemon_deps(at(15, 40)), _job("kr_fo_monitor"))
    assert seen["accounts"] == [ACCT]
    assert out == {"accounts": {ACCT: {"exits": 1, "maintain": 0, "triggers": 0, "entries": 0,
                                       "recalled": 0, "halted": False}}}  # fmt: skip
    control.set_flag(conn, "paused", True)
    assert jobs.monitor_job(daemon_deps(at(10, 0)), _job("kr_fo_monitor")) == {
        "skipped": "일시정지"}


def test_monitor_job_reports_account_errors(conn, monkeypatch):
    monkeypatch.setattr(engine, "tick", lambda ctx, **kw: {"accounts": {ACCT: {"error": "boom"}}})
    out = jobs.monitor_job(daemon_deps(at(10, 0)), _job("kr_fo_monitor"))
    assert out["error"] == f"{ACCT}: boom"


# --- 세션 잡·요청 ------------------------------------------------------------------------------


def test_session_job_skips_holiday_and_paused(conn, monkeypatch):
    calls: list = []

    def fake_run(settings, conn_, *, ports, master, **kw):
        calls.append(kw)
        return {"date": "2026-10-27", "skipped": None, "dry_run": kw["dry_run"],
                "decision": {"decision": {"intent": "HOLD"}}}  # fmt: skip

    monkeypatch.setattr(fo_session, "run", fake_run)
    now = at(8, 20)
    assert jobs.session_job(daemon_deps(now, accounts=[]), _job("kr_fo_session")) == {
        "skipped": "파생 계좌 없음"}
    assert jobs.session_job(daemon_deps(now, holidays={TODAY}), _job("kr_fo_session")) == {
        "skipped": "휴장일"}
    assert jobs.session_job(daemon_deps(SAT), _job("kr_fo_session")) == {"skipped": "주말"}
    control.set_flag(conn, "paused", True)
    assert jobs.session_job(daemon_deps(now), _job("kr_fo_session")) == {"skipped": "일시정지"}
    assert calls == []
    control.set_flag(conn, "paused", False)
    out = jobs.session_job(daemon_deps(now), _job("kr_fo_session"))
    assert out == {"date": "2026-10-27", "skipped": None, "error": None, "intent": "HOLD",
                   "dry_run": False}  # fmt: skip
    assert len(calls) == 1 and calls[0]["cancel"] is not None


def test_session_cap_pipeline_error_is_skipped(conn, monkeypatch):
    def capped(*a, **k):
        raise PipelineError("오늘 fo 세션 상한(3) 도달 — 세션을 띄우지 않습니다")

    monkeypatch.setattr(fo_session, "run", capped)
    deps = daemon_deps(at(8, 20))
    assert jobs.session_job(deps, _job("kr_fo_session")) == {
        "skipped": "오늘 fo 세션 상한(3) 도달 — 세션을 띄우지 않습니다"}
    assert jobs.session_request(deps, {}, lambda s: None) == {
        "skipped": "오늘 fo 세션 상한(3) 도달 — 세션을 띄우지 않습니다"}


# --- 재평가·청산 요청 ---------------------------------------------------------------------------


def _reeval_params(conn, ctx, pos):
    tid = triggers.record(conn, pos, triggers.Trigger("move", {}, Decimal("365")), now=at(10, 0))
    return tid, {"account_id": ACCT, "position_id": pos.id, "expected_version": pos.version,
                 "request_id": "r", "round": 1, "trigger_id": tid, "date": TODAY.isoformat()}


def _marker(conn, tid):
    return conn.execute("SELECT handled_at, session_id FROM triggers WHERE id = ?",
                        (tid,)).fetchone()  # fmt: skip


def test_reevaluate_request_outside_fo_hours_releases_trigger(conn, monkeypatch):
    ctx = make_ctx(conn)
    pos = _held_position(conn, ctx, qty=2)
    tid, p = _reeval_params(conn, ctx, pos)
    out = jobs.reevaluate_request(daemon_deps(at(15, 50)), p, lambda s: None)
    assert out == {"skipped": "파생 장 밖"}
    n = conn.execute("SELECT COUNT(*) FROM triggers WHERE id = ? AND session_id LIKE '보류:%'",
                     (tid,)).fetchone()[0]  # fmt: skip
    assert n == 1 and _marker(conn, tid)["session_id"] == "보류: 파생 장 밖"
    # 일시정지(옛 `should_skip`)도 보류로 닫는다
    tid2, p2 = _reeval_params(conn, ctx, pos)
    control.set_flag(conn, "paused", True)
    out = jobs.reevaluate_request(daemon_deps(at(10, 30)), p2, lambda s: None)
    assert out == {"skipped": "일시정지"} and _marker(conn, tid2)["session_id"] == "보류: 일시정지"
    control.set_flag(conn, "paused", False)
    # 15:40 은 파생 장 안 — reeval.run 까지 간다(옛 `_auto_gate` 의 파생 시계)
    seen: list = []
    monkeypatch.setattr(reeval, "run", lambda c, params, **kw: seen.append(
        (c.accounts[0].id, params["trigger_id"], kw)) or {"ok": True})  # fmt: skip
    tid3, p3 = _reeval_params(conn, ctx, pos)
    deps = daemon_deps(at(15, 40))
    assert jobs.reevaluate_request(deps, p3, lambda s: None) == {"ok": True}
    assert seen[0][:2] == (ACCT, tid3) and seen[0][2]["cancel"] is deps.stop_event


def test_close_request_rechecks_state(conn):
    p = {"account_id": ACCT, "position_id": 1}
    assert jobs.close_request(daemon_deps(at(16, 0)), p, lambda s: None) == {
        "skipped": "파생 장 밖"}
    ctx = make_ctx(conn, now=at(10, 0), market=_market(at(10, 0)))
    pos = _held_position(conn, ctx, qty=2)
    deps = daemon_deps(at(10, 0), brokers=ctx.brokers, market=ctx.market_client)
    p = {"account_id": ACCT, "position_id": pos.id}
    assert jobs.close_request(deps, {"account_id": "nope", "position_id": pos.id},
                              lambda s: None) == {"skipped": "파생 계좌 nope 없음"}  # fmt: skip
    assert jobs.close_request(deps, {"account_id": ACCT, "position_id": 999},
                              lambda s: None) == {"skipped": "포지션 999 없음"}  # fmt: skip
    control.set_flag(conn, "paused", True)
    out = jobs.close_request(deps, p, lambda s: None)
    assert out["status"] == "recorded" and ctx.brokers[ACCT].orders == []
    control.set_flag(conn, "paused", False)
    out = jobs.close_request(deps, p, lambda s: None)
    assert out["status"] == "accepted" and ctx.brokers[ACCT].orders[0].side == "SELL"
    with store.tx(conn):
        pid = store.insert_position(conn, account_id=ACCT, code="105W11", kind="FUTURES",
                                    contract_side="LONG", direction="LONG", vehicle="FUTURES",
                                    status="awaiting_settlement", qty_held=1)  # fmt: skip
    out = jobs.close_request(deps, {"account_id": ACCT, "position_id": pid}, lambda s: None)
    assert out == {"skipped": "포지션 상태 awaiting_settlement — 자동 청산 대상 아님"}


# --- 대조 잡 -------------------------------------------------------------------------------------


def test_reconcile_job_syncs_reconciles_and_refreshes(conn):
    assert jobs.reconcile_job(daemon_deps(at(16, 40), accounts=[]), _job("kr_fo_reconcile")) == {
        "skipped": "파생 계좌 없음"}
    assert jobs.reconcile_job(daemon_deps(SAT), _job("kr_fo_reconcile")) == {"skipped": "주말"}
    out = jobs.reconcile_job(daemon_deps(at(16, 40)), _job("kr_fo_reconcile"))
    acc = out["accounts"][ACCT]
    assert acc["fills"]["failed_markets"] == [] and acc["reconcile"]["mismatches"] == []
    assert acc["snapshot"] is True
    n = conn.execute("SELECT COUNT(*) FROM fo_nav_snapshot WHERE account_id = ?",
                     (ACCT,)).fetchone()[0]  # fmt: skip
    assert n == 1


def test_reconcile_request_shares_job_body(conn):
    """큐 kind `fo_reconcile`(catch_up="queue" 의 대상)은 잡과 같은 대조를 돈다."""
    assert jobs.reconcile_request(daemon_deps(at(16, 40), accounts=[]), {}, None) == {
        "skipped": "파생 계좌 없음"}
    out = jobs.reconcile_request(daemon_deps(at(16, 40)), {}, None)
    assert out["accounts"][ACCT]["snapshot"] is True


def test_fo_ctx_master_failure_is_empty(conn, monkeypatch):
    from trader.kernel.broker import fo_master

    def boom(**kw):
        raise OSError("다운로드 실패")

    monkeypatch.setattr(fo_master, "load", boom)
    ctx = jobs._fo_ctx(daemon_deps(at(10, 0)), conn)
    assert ctx.master == [] and [a.id for a in ctx.accounts] == [ACCT]


# --- Review Focus 3·4·5 -------------------------------------------------------------------------


def _tick(ctx, calls=None):
    sink = calls if calls is not None else []
    return engine.tick(ctx, request_reeval=lambda c, p: sink.append(p))


def test_paused_engine_consumes_nothing(conn):
    """일시정지 중 엔진은 상태를 소비·변경하지 않는다: 보류 진입·REPLACE 플래그 유지, 트리거 없음,
    유지 축소는 기록만, 정산 전이 없음, halted 플래그 없음, 주문·취소 없음."""
    now = at(9, 5)
    ctx = make_ctx(conn, now=now, market=_market(now), max_contracts=20,
                   reeval={"periodic_max_per_day": 4})  # fmt: skip
    _pending_flag(conn)
    big = _held_position(conn, ctx, qty=12, qty_target=12)  # 노출 배수 위반 → 축소 대상
    with store.tx(conn):
        sid = store.insert_position(conn, account_id=ACCT, code="205W11350", kind="CALL",
                                    contract_side="LONG", direction="LONG",
                                    vehicle="OPTION_BUY", status="awaiting_settlement",
                                    qty_held=1, qty_target=1,
                                    last_trade_date="2026-10-26")  # fmt: skip
    rkey = reeval.replace_key(ACCT, big.id)
    control.set_flag(conn, rkey, {"date": TODAY.isoformat(), "round": 1,
                                  "envelope": env(decision()).model_dump(mode="json")})
    b = balance([holding(qty=12)])  # 정산 대상 콜은 잔고에 없음
    ctx.brokers[ACCT].balance = b.model_copy(
        update={"cash": b.cash.model_copy(update={"realized_today": Decimal("-9000000")})})
    control.set_flag(conn, "paused", True)
    calls: list = []
    out = _tick(ctx, calls)
    acc = out["accounts"][ACCT]
    assert acc["entries"] == [{"pending": "r1", "held": "paused"}]
    assert control.get_flag(conn, f"{tools.FO_PENDING_FLAG}{ACCT}") is not None
    assert control.get_flag(conn, rkey) is not None
    assert calls == [] and conn.execute("SELECT COUNT(*) FROM triggers").fetchone()[0] == 0
    assert acc["maintain"] and all(m["status"] == "recorded" for m in acc["maintain"])
    assert store.get_position(conn, big.id).qty_target == 12
    assert store.get_position(conn, sid).status == "awaiting_settlement"
    assert control.get_flag(conn, f"fo_halted:{ACCT}") is None and acc["halted"] is False
    assert ctx.brokers[ACCT].orders == [] and ctx.brokers[ACCT].cancelled == []


def test_pending_entry_runs_once_in_window(conn, monkeypatch):
    """진입 창이 열리면 플래그를 **먼저** 지우고 실행 직전 전체 재계산(`run_leg`)으로 1회 진입한다.
    다음 틱은 다시 내지 않는다. cutoff 뒤면 버린다."""
    from trader.features.fo import executor

    now = at(9, 5)
    ctx = make_ctx(conn, now=now, market=_market(now), reeval={"periodic_max_per_day": 0})
    _pending_flag(conn)
    key = f"{tools.FO_PENDING_FLAG}{ACCT}"
    seen: list = []
    real = executor.run_leg

    def spy(c, a, e, **kw):
        seen.append((control.get_flag(conn, key), kw.get("source"), kw.get("round")))
        return real(c, a, e, **kw)

    monkeypatch.setattr(executor, "run_leg", spy)
    out = _tick(ctx)
    assert seen == [(None, "pending", 0)]  # 플래그가 지워진 뒤 run_leg(재계산) 1회
    assert out["accounts"][ACCT]["entries"][0]["results"][0]["status"] == "accepted"
    assert [o.side for o in ctx.brokers[ACCT].orders] == ["BUY"]
    _tick(ctx)
    assert len(ctx.brokers[ACCT].orders) == 1 and len(seen) == 1
    # cutoff 뒤의 보류분은 실행 없이 버린다
    _pending_flag(conn)
    ctx.now = lambda: at(15, 32)
    ctx.market_client = _market(at(15, 32))
    out = _tick(ctx)
    assert "cutoff" in out["accounts"][ACCT]["entries"][0]["discarded"]
    assert control.get_flag(conn, key) is None and len(seen) == 1


@pytest.mark.parametrize(("fills_fail", "dry_run", "settled"),
                         [(True, False, False), (False, True, False), (False, False, True)])
def test_settle_requires_fills_ok_and_not_dry_run(conn, fills_fail, dry_run, settled):
    ctx = make_ctx(conn, market=_market(at(10, 30)), reeval={"periodic_max_per_day": 0},
                   dry_run=dry_run)  # fmt: skip
    pos = _held_position(conn, ctx, qty=1, code="205W11350", kind="CALL", vehicle="OPTION_BUY",
                         last_trade_date="2026-10-26", entry_premium=Decimal("5"))  # fmt: skip
    with store.tx(conn):
        store.transition(conn, pos.id, expected_version=pos.version, status="awaiting_settlement")
    ctx.brokers[ACCT].balance = balance()  # 최종거래일 다음 날 잔고에 없음
    if fills_fail:
        ctx.brokers[ACCT].fail = {"get_fills"}
    _tick(ctx)
    _tick(ctx)
    cur = store.get_position(conn, pos.id)
    assert (cur.status == "settled") is settled
    notes = [m for m in ctx.notifier.msgs if "파생 정산" in m]
    if settled:
        assert cur.qty_held == 0 and len(notes) == 1 and "HTS" in notes[0]
    else:
        assert cur.status == "awaiting_settlement" and notes == []
