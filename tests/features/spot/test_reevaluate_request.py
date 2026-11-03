"""큐 `reevaluate` 본문 `jobs.reevaluate_request` — 트리거 확인·뉴스 재수집·종목 락·형제 트리거.

옛 `tests/test_daemon_queue.py` 에서 이식:
`reevaluate_request_runs_session_and_marks_sibling_triggers`(L224),
`reevaluate_request_passes_cancel_and_skips_when_stopping`(L378),
`reevaluate_runs_under_same_ticker_lock_as_analyze`(L482, Ruling P11: analyze 잡 자리에
`locks.lock_for` 를 직접 잡는 합성 보유자),
`analyze_ticker_request_runs_one_session_with_dry_run_precedence`(L128 — analyze_ticker kind 는
M7, 재평가에 해당하는 dry_run 우선 규칙만). 치환:
`daemon._reevaluate_request(deps, p)` → `jobs.reevaluate_request(deps, p, progress)`,
`deps.session_runner` → `jobs.run_session` monkeypatch(DaemonDeps 에 러너 칸이 없다),
`repo.insert_trigger` → `spot.ledger.insert_trigger`, `daemon._ticker_lock(deps, …)` →
`locks.lock_for(…)`, `_queue_tick` → `queue_jobs.queue_tick(lanes={"reeval"})`. 시각은 토요일
NOW 대신 평일 장중(재평가 본문이 장 마감·장 없는 날을 건너뛰므로 — Review Focus 2).
"""

import logging
import threading
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.features.spot.conftest import make_account
from tests.features.spot.helpers import ok_decision
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.accounts.settings import to_broker_account
from trader.features.spot import jobs as spot_jobs
from trader.features.spot import ledger as spot_ledger
from trader.features.spot import locks
from trader.features.spot.feature import FEATURE
from trader.features.spot.schemas import Decision
from trader.kernel.broker.fake import FakeBroker, FakeMarket
from trader.kernel.broker.models import Cash, Position
from trader.kernel.config import Secrets
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.queue_jobs import QueueState, queue_tick
from trader.kernel.daemon.runner import DaemonDeps
from trader.kernel.ledger import db
from trader.kernel.session.run_store import run_dir, write_checkpoint
from trader.kernel.session.runner import SessionResult

D = date(2026, 10, 9)  # 금요일
NOW = datetime(2026, 10, 9, 2, 0, tzinfo=UTC)  # 11:00 KST, 장중
AFTER_CLOSE = datetime(2026, 10, 9, 7, 0, tzinfo=UTC)  # 16:00 KST, 마감 뒤
REEVAL = frozenset({"reeval"})


class _Port(AccountsPortImpl):
    """합성 계좌 + 가짜 브로커·시세(네트워크 없음)."""

    def __init__(self, accounts, brokers, market):
        super().__init__(accounts, Secrets())
        self._brokers, self._market = brokers, market

    def brokers(self):
        return self._brokers

    def market_client(self):
        return self._market


class _Inputs:
    def __init__(self):
        self.calls = []

    def collect(self, **kw):
        self.calls.append(kw)
        return {}


def _deps(settings, project, *, now=NOW, dry_run=False, ports=None):
    return DaemonDeps(
        settings=settings,
        schedule=None,
        conn_factory=db.connect,
        state=project / "state",
        now=lambda: now,
        markets=["KR"],
        gates={},
        stop_event=threading.Event(),
        dry_run=dry_run,
        ports=ports or {},
        queue=QueueState(kinds=jobq.kinds_of([FEATURE])),
    )


def _runner(monkeypatch, seen, *, session_id="s-re"):
    def runner(spec, kind, lane_ctx, **kw):
        seen.append((spec, kw))
        return SessionResult(ok=True, session_id=session_id, fake=True)

    monkeypatch.setattr(spot_jobs, "run_session", runner)


def _trig(conn, kind="move_up", detail=None, price=Decimal("76000")):
    return spot_ledger.insert_trigger(
        conn,
        market="KR",
        ticker="005930",
        kind=kind,
        detail=detail or {},
        price=price,
        decision_id=None,
        account_id="paper-main",
        created_at=NOW.isoformat(),
    )


def _noop(text):
    return None


def test_reevaluate_kind_is_registered_on_reeval_lane():
    q = jobq.kinds_of([FEATURE])["reevaluate"]
    assert (q.label, q.lane, q.priority, q.trading, q.restart_requeue) == (
        "재평가", "reeval", 10, True, False,
    )  # fmt: skip
    assert q.params_model is spot_jobs.ReevaluateParams and q.body is spot_jobs.reevaluate_request


def test_reevaluate_request_runs_session_and_marks_sibling_triggers(
    project, conn, settings, monkeypatch
):
    deps = _deps(settings, project)
    seen = []
    _runner(monkeypatch, seen)
    t1 = _trig(conn, kind="move_up", detail={"pct": 0.08})
    t2 = _trig(conn, kind="take_profit")
    out = spot_jobs.reevaluate_request(
        deps,
        {
            "market": "KR",
            "ticker": "005930",
            "exchange": None,
            "trigger_id": t1,
            "trigger_ids": [t2],
        },
        _noop,
    )
    spec = seen[0][0]
    assert out["ok"] and spec.kind == "resume" and spec.extra["trigger"]["kind"] == "move_up"
    rows = conn.execute("SELECT id, handled_at, session_id FROM triggers ORDER BY id").fetchall()
    assert all(r["handled_at"] for r in rows) and rows[1]["session_id"] == "s-re"
    # 이미 처리된 트리거는 다시 세션을 띄우지 않는다
    assert spot_jobs.reevaluate_request(
        deps, {"market": "KR", "ticker": "005930", "trigger_id": t1}, _noop
    )["skipped"]


def test_reevaluate_request_passes_cancel_and_skips_when_stopping(
    project, conn, settings, monkeypatch
):
    """전체 검토 P1: 재평가 세션에 종료 신호(cancel)를 넘기고, 종료 중이면 세션을 안 띄운다."""
    seen = []
    _runner(monkeypatch, seen, session_id="s")
    deps = _deps(settings, project)
    tid = _trig(conn, kind="move_7pct", price=None)
    p = {"market": "KR", "ticker": "005930", "trigger_id": tid}
    spot_jobs.reevaluate_request(deps, p, _noop)
    assert [kw.get("cancel") for _, kw in seen] == [deps.stop_event]
    deps.stop_event.set()
    out = spot_jobs.reevaluate_request(
        deps, {**p, "trigger_id": _trig(conn, kind="move_7pct", price=None)}, _noop
    )
    assert out == {"skipped": "종료 중"} and len(seen) == 1


def test_reevaluate_runs_under_same_ticker_lock_as_analyze(project, conn, settings, monkeypatch):
    """PR #37 리뷰: 빠른 레인의 재평가는 같은 (시장, 종목) analyze 세션과 같은 락 안에서 돈다.
    Ruling P11: analyze 잡(M7) 자리에 `lock_for` 를 직접 쥔 합성 보유자 — 보유자가 놓을 때까지
    재평가는 기다린다."""
    deps = _deps(settings, project)
    lock = locks.lock_for("KR", "005930")
    assert lock is locks.lock_for("KR", "005930")
    assert lock is not locks.lock_for("KR", "000660")
    held = []

    def runner(spec, kind, lane_ctx, **kw):
        held.append(lock.locked())
        return SessionResult(ok=True, session_id="s")

    monkeypatch.setattr(spot_jobs, "run_session", runner)
    tid = _trig(conn, kind="move", price=None)
    out: dict = {}
    lock.acquire()  # 합성 보유자(같은 종목 analyze 세션)
    try:
        t = threading.Thread(
            target=lambda: out.update(
                spot_jobs.reevaluate_request(
                    deps, {"market": "KR", "ticker": "005930", "trigger_id": tid}, _noop
                )
            ),
            daemon=True,
        )
        t.start()
        t.join(0.3)
        assert t.is_alive() and held == []  # 락을 기다린다
    finally:
        lock.release()
    t.join(2)
    assert not t.is_alive()
    assert held == [True] and not lock.locked() and out["ok"]


def test_reevaluate_request_daemon_dry_run_takes_precedence(project, conn, settings, monkeypatch):
    """옛 analyze_ticker dry_run 우선 규칙: 데몬 --dry-run 을 요청이 뒤집지 못한다 — 요청 모델에
    dry_run 칸이 없고(거부), 큐로 돈 세션은 데몬 값을 받는다."""
    seen = []
    _runner(monkeypatch, seen, session_id="s")
    deps = _deps(settings, project, dry_run=True)
    tid = _trig(conn)
    p = {"market": "KR", "ticker": "005930", "trigger_id": tid}
    with pytest.raises(ValueError, match="dry_run"):
        jobq.enqueue(
            conn, deps.queue.kinds, "reevaluate", {**p, "dry_run": False}, source="web", now=NOW
        )
    jobq.enqueue(conn, deps.queue.kinds, "reevaluate", p, source="web", now=NOW)
    queue_tick(deps, lanes=REEVAL)
    assert seen[0][0].dry_run is True  # 데몬 --dry-run 을 웹이 뒤집지 못한다
    assert seen[0][0].tickers == [{"ticker": "005930", "exchange": None}]
    req = jobq.list_recent(conn)[0]
    assert req["status"] == "ok" and req["result"]["ok"] is True


def test_reevaluate_after_close_or_handled_trigger_is_skipped_without_order(
    project, conn, settings, monkeypatch
):
    """Review Focus 2: 장 마감 뒤이거나 이미 처리된 트리거면 세션·주문 없이 skipped."""
    seen = []
    _runner(monkeypatch, seen)
    inputs = _Inputs()
    tid = _trig(conn, kind="news")
    p = {"market": "KR", "ticker": "005930", "trigger_id": tid}
    late = _deps(settings, project, now=AFTER_CLOSE, ports={"inputs": inputs})
    assert spot_jobs.reevaluate_request(late, p, _noop) == {"skipped": "장 마감"}
    assert spot_ledger.get_trigger(conn, tid)["handled_at"] is None
    spot_ledger.mark_trigger_handled(conn, tid, "s-old", handled_at=NOW.isoformat())
    deps = _deps(settings, project, ports={"inputs": inputs})
    out = spot_jobs.reevaluate_request(deps, p, _noop)
    assert out == {"skipped": "이미 처리된 트리거"}
    assert seen == [] and inputs.calls == []
    assert conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"] == 0


def test_news_trigger_recollects_inputs_before_session(project, conn, settings, monkeypatch):
    """뉴스 재평가(Phase 23): 트레이더가 새 뉴스를 보도록 입력을 강제 재수집한 뒤 세션을 띄운다.
    수집 포트가 없거나 실패해도 기존 입력으로 재평가한다."""
    order = []

    def runner(spec, kind, lane_ctx, **kw):
        order.append("session")
        return SessionResult(ok=True, session_id="s")

    monkeypatch.setattr(spot_jobs, "run_session", runner)

    class Inputs(_Inputs):
        def collect(self, **kw):
            order.append("collect")
            return super().collect(**kw)

    inputs = Inputs()
    deps = _deps(settings, project, ports={"inputs": inputs})
    p = {"market": "KR", "ticker": "005930", "exchange": None, "trigger_id": _trig(conn, "news")}
    assert spot_jobs.reevaluate_request(deps, p, _noop)["ok"]
    assert order == ["collect", "session"]
    kw = inputs.calls[0]
    assert (kw["ticker"], kw["market"], kw["exchange"], kw["force"]) == ("005930", "KR", None, True)
    assert kw["asof"] == NOW and kw["conn_factory"] is deps.conn_factory

    class Boom:
        def collect(self, **kw):
            raise RuntimeError("수집 실패")

    order.clear()
    for ports in ({}, {"inputs": Boom()}):
        d = _deps(settings, project, ports=ports)
        assert spot_jobs.reevaluate_request(d, {**p, "trigger_id": _trig(conn, "news")}, _noop)[
            "ok"
        ]
    assert order == ["session", "session"]


def test_reevaluate_start_failure_marks_triggers_failed_and_notifies(
    project, conn, settings, monkeypatch
):
    """세션 기동 실패(상한·감속 등 예외): 인라인 경로와 같이 트리거를 '실패:' 로 닫고 알린 뒤
    큐 요청은 error 로 남는다 — 미처리로 두면 감시 틱마다 다시 등록된다."""

    def boom(*a, **kw):
        raise RuntimeError("세션 상한")

    monkeypatch.setattr(spot_jobs, "run_session", boom)
    sent = []
    deps = replace(_deps(settings, project), notifier=SimpleNamespace(send=sent.append))
    t1, t2 = _trig(conn), _trig(conn, kind="take_profit")
    p = {"market": "KR", "ticker": "005930", "trigger_id": t1, "trigger_ids": [t2]}
    jobq.enqueue(conn, deps.queue.kinds, "reevaluate", p, source="web", now=NOW)
    queue_tick(deps, lanes=REEVAL)
    req = jobq.list_recent(conn)[0]
    assert req["status"] == "error" and "세션 상한" in req["error"]
    rows = conn.execute("SELECT handled_at, session_id FROM triggers ORDER BY id").fetchall()
    assert all(r["handled_at"] and r["session_id"].startswith("실패:") for r in rows)
    assert len(sent) == 1 and "재평가 실패" in sent[0]


# --- Task 11 인계: monitor_body → enqueue_from_job → 등록된 kind ------------------------


def _decision_today(conn):
    dec = Decision(**ok_decision(action="HOLD", stop_loss=60000.0, take_profit=None))
    d = run_dir(D, "KR", "005930")
    data = dec.model_dump()
    write_checkpoint(d, "30_decision", {"schema_version": 1, "data": data, "call": None})
    spot_ledger.ingest_checkpoint(conn, D, "KR", "005930", "30_decision", {"data": data})


def test_monitor_body_enqueues_registered_reevaluate_and_queue_runs_it(
    project, conn, settings, monkeypatch, caplog
):
    """데몬 감시 틱의 트리거가 등록된 `reevaluate` kind 로 큐에 들어가고(경고 없음), 재평가
    레인이 꺼내 세션을 돌려 트리거를 처리한다(monitor 의 params 모양 = ReevaluateParams)."""
    accounts = [make_account("paper-main", "paper", "auto", markets=["KR"])]
    pos = Position(
        ticker="005930",
        market="KR",
        qty=10,
        avg_price=Decimal("70000"),
        current_price=Decimal("76000"),
        eval_amount=Decimal("760000"),
        pnl_pct=76000 / 70000 - 1,
    )
    cash = {"KR": Cash(currency="KRW", available=Decimal("10000000"), total=Decimal("10000000"))}
    brokers = {
        a.id: FakeBroker(to_broker_account(a), positions=[pos], cash=cash) for a in accounts
    }
    port = _Port(accounts, brokers, FakeMarket(prices={"005930": Decimal("76000")}))
    deps = _deps(settings, project, ports={"accounts": port})
    _decision_today(conn)
    seen = []
    _runner(monkeypatch, seen)
    with caplog.at_level(logging.WARNING):
        out = spot_jobs.monitor_body(deps, SimpleNamespace(market="KR"))
    assert out["triggers"] >= 1 and out.get("queued") == 1
    assert not [r for r in caplog.records if "큐 등록 실패" in r.getMessage()]
    req = jobq.list_recent(conn)[0]
    assert (req["kind"], req["status"], req["source"]) == ("reevaluate", "queued", "daemon")
    assert set(req["params"]) == {"market", "ticker", "exchange", "trigger_id", "trigger_ids"}
    assert seen == []  # 틱 안에서는 세션을 돌리지 않는다
    assert queue_tick(deps, lanes=REEVAL) == {"request": req["id"], "kind": "reevaluate"}
    assert jobq.list_recent(conn)[0]["status"] == "ok" and len(seen) == 1
    rows = conn.execute("SELECT handled_at, session_id FROM triggers").fetchall()
    assert rows and all(r["handled_at"] and r["session_id"] == "s-re" for r in rows)
    assert brokers["paper-main"].orders == []


def test_reevaluate_start_failure_still_notifies_when_bookkeeping_raises(
    project, conn, settings, monkeypatch
):
    """S2: 기동 실패 정리(트리거 마감·종목 표기)가 다시 예외를 내도 원래 예외가 알림·요청
    오류로 올라간다(정리 예외가 원인을 가리지 않는다)."""
    from trader.features.spot import labels

    def boom(*a, **kw):
        raise RuntimeError("세션 상한")

    def broken(*a, **kw):
        raise OSError("원장 잠김")

    monkeypatch.setattr(spot_jobs, "run_session", boom)
    monkeypatch.setattr(spot_ledger, "mark_trigger_handled", broken)
    monkeypatch.setattr(labels, "resolve", broken)
    sent = []
    deps = replace(_deps(settings, project), notifier=SimpleNamespace(send=sent.append))
    t1 = _trig(conn)
    p = {"market": "KR", "ticker": "005930", "trigger_id": t1}
    jobq.enqueue(conn, deps.queue.kinds, "reevaluate", p, source="web", now=NOW)
    queue_tick(deps, lanes=REEVAL)
    req = jobq.list_recent(conn)[0]
    assert req["status"] == "error" and "세션 상한" in req["error"]
    assert len(sent) == 1 and "재평가 실패" in sent[0] and "세션 상한" in sent[0]
