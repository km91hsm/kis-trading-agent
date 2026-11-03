"""reflect 잡·큐 kind·`reflect.mature` 훅·놓친 cron 따라잡기. 옛 test_daemon 4건 + hedge 1건 이식,
신규: 시세 클라이언트 없음·훅 발화·catch-up·A12(market 존중)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.features.evaluation.helpers import seed_matured
from tests.features.evaluation.test_outcomes import DAYS, _decision, _store
from tests.kernel.daemon.test_runner import make_deps
from trader.features.evaluation import jobs, reflect
from trader.features.evaluation.feature import FEATURE
from trader.features.evaluation.jobs import REFLECT_JOB
from trader.features.evaluation.memory import store
from trader.features.regime import market_view
from trader.features.scan import api as scan_api
from trader.kernel.broker.fake import FakeMarket
from trader.kernel.broker.models import Candle
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon import queue_jobs as qj
from trader.kernel.daemon.jobs import build
from trader.kernel.daemon.queue_jobs import QueueState
from trader.kernel.registry import discover
from trader.kernel.registry.types import Hook
from trader.kernel.session.runner import PipelineError, SessionResult

NOW = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)  # 월 16:00 KST


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


@pytest.fixture(autouse=True)
def _fake_claude(monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")


def _market(closes=None):
    closes = closes or [100] * 5
    start = date(2026, 10, 26)
    cs = [
        Candle(date=start + timedelta(days=i), open=Decimal(1), high=Decimal(1), low=Decimal(1),
               close=Decimal(c), volume=5)
        for i, c in enumerate(closes)
    ]  # fmt: skip
    return FakeMarket(candles={"069500": cs, "SPY": cs}), cs


def _deps(project, settings, *, mc="fake", ml=None, **kw):
    if mc == "fake":
        mc = _market()[0]
    ports = {"accounts": SimpleNamespace(market_client=lambda: mc)}
    if ml is not None:
        ports["ml"] = ml
    kw.setdefault("notifier", Sent())
    return make_deps(
        project, settings=settings, now=lambda: NOW, markets=["KR", "US"], ports=ports,
        queue=QueueState(kinds=jobq.kinds_of([FEATURE])), **kw,
    )  # fmt: skip


class Ml:
    def __init__(self, ret=3):
        self.calls, self.ret = [], ret

    def snapshot(self, markets, *, tickers=None, extra_targets=None):
        self.calls.append((list(markets), extra_targets))
        if isinstance(self.ret, Exception):
            raise self.ret
        return self.ret


def test_reflect_body_skips_when_nothing_matured(project, settings):
    out = jobs.reflect_body(_deps(project, settings))
    assert out == {
        "features": None, "matured": 0, "views_matured": 0, "shadow_matured": 0,
        "sessions": 0, "errors": [], "pruned": 0,
    }  # fmt: skip


def test_reflect_job_snapshots_features_first(project, settings):
    ml = Ml()
    deps = _deps(project, settings, ml=ml)
    r = jobs.reflect_body(deps)
    assert ml.calls == [(["KR", "US"], [])] and r["features"] == 3
    ml.ret = RuntimeError("KIS 장애")
    r = jobs.reflect_body(deps)  # 스냅샷 실패는 통보하고 반성은 계속
    assert r["features"] is None and any("피처 스냅샷 실패" in m for m in deps.notifier.msgs)


def test_reflect_snapshot_includes_open_window_tickers_outside_universe(
    project, settings, monkeypatch
):
    ml = Ml()
    deps = _deps(project, settings, ml=ml)
    conn = deps.conn_factory()
    ids = seed_matured(conn, 3, now=NOW)
    for eid, (market, ticker, ex) in zip(
        ids, [("KR", "247540", None), ("US", "RIVN", "NAS"), ("US", "AAPL", None)], strict=True
    ):
        conn.execute(
            "UPDATE memory_entries SET market=?, ticker=?, outcome_at=NULL WHERE id=?",
            (market, ticker, eid),
        )
        if ex:
            conn.execute(
                "INSERT INTO ticker_exchanges(market, ticker, exchange, updated_at) "
                "VALUES (?,?,?,?)", (market, ticker, ex, NOW.isoformat()),
            )  # fmt: skip
    conn.commit()
    monkeypatch.setattr(jobs, "load_universe", lambda: SimpleNamespace(kr=[], us=[
        SimpleNamespace(symbol="AAPL")
    ]))  # fmt: skip
    conn.close()
    jobs.reflect_body(deps)
    assert ml.calls[0][1] == [("247540", "KR", None), ("RIVN", "US", "NAS")]


def test_reflect_body_continues_after_market_error(project, settings, monkeypatch):
    calls = []

    def fake_run_reflect(ctx, *, market, **kw):
        calls.append(market)
        if market == "KR":
            raise PipelineError("세션 상한")
        return SessionResult(ok=True, session_id="s", stages_written=["_reflect"])

    monkeypatch.setattr(jobs, "pending", lambda conn, **kw: [{"entry_id": 1}])
    monkeypatch.setattr(jobs, "run_reflect", fake_run_reflect)
    deps = _deps(project, settings)
    out = jobs.reflect_body(deps)
    assert calls == ["KR", "US"] and out["sessions"] == 1 and len(out["errors"]) == 1
    assert any("복기 실패" in m and "세션 상한" in m for m in deps.notifier.msgs)


def test_reflect_body_matures_market_views(project, settings):
    mc, cs = _market([100 - i for i in range(5)])
    deps = _deps(project, settings, mc=mc)
    conn = deps.conn_factory()
    market_view.record_view(
        conn, today=cs[0].date, market="KR",
        view={"stance": "RISK_OFF", "hedge_weight": 0.1, "horizon_days": 2},
    )  # fmt: skip
    conn.commit()
    out = jobs.reflect_body(deps)
    assert out["views_matured"] == 1
    assert conn.execute("SELECT hit FROM market_views").fetchone()["hit"] == 1
    conn.close()


def test_reflect_body_without_market_client_skips_snapshot_and_still_matures(project, settings):
    ml = Ml()
    deps = _deps(project, settings, mc=None, ml=ml)
    conn = deps.conn_factory()
    seed_matured(conn, 1, start=date(2026, 10, 9), now=NOW)
    # 미만기 엔트리 1건 + 피처 스토어 종가: 시세 클라이언트가 없어도 만기 채점은 돈다
    _store({"A": [100] * 5 + [104] * 25})
    eid = _decision(conn, 1, "A", "BUY", DAYS[0])
    conn.commit()
    conn.close()
    out = jobs.reflect_body(deps)
    assert ml.calls == [] and out["features"] is None and deps.notifier.msgs == []
    assert out["sessions"] == 1 and out["errors"] == [] and out["matured"] == 1
    conn = deps.conn_factory()
    assert store.get_entry(conn, eid)["outcome_at"] is not None
    conn.close()


def test_reflect_fires_mature_hook(project, settings):
    seen = []
    hooks = {"reflect.mature": (Hook("reflect.mature", lambda **kw: seen.append(kw)),)}
    deps = _deps(project, settings, hooks=hooks)
    jobs.reflect_body(deps)
    (kw,) = seen
    assert kw["matured"] == [] and kw["deps"] is deps and kw["today"] == date(2026, 11, 2)


@pytest.mark.parametrize("target", ["theme", "shadow"])
def test_reflect_mature_failure_leaves_no_open_write_transaction(
    project, settings, monkeypatch, target
):
    """테마·섀도 채점이 쓰기 뒤 커밋 전에 실패해도 열린 쓰기 트랜잭션을 남기지 않는다(잠금 방지)."""

    def boom(conn, *a, **kw):
        conn.execute("UPDATE memory_entries SET round=round")  # 암묵 BEGIN
        raise RuntimeError("채점 장애")

    if target == "theme":
        monkeypatch.setattr(scan_api, "theme_mature", boom)
    else:
        monkeypatch.setattr(jobs.shadow, "mature", boom)
    seen = []
    hook = Hook("reflect.mature", lambda *, conn, **kw: seen.append(conn.in_transaction))
    deps = _deps(project, settings, hooks={"reflect.mature": (hook,)})
    jobs.reflect_body(deps)
    assert seen == [False]


def test_reflect_catch_up_enqueues_queue_kind(project, settings):
    deps = _deps(project, settings)
    assert qj.misfire_grace_for(REFLECT_JOB) == 21600
    schedule = load("schedule", build_registry(discover()), path=project / "config" / "x.yaml")
    ctx = SimpleNamespace(gates={"market": lambda job: None})
    job = {j.id: j for j in build([FEATURE], schedule=schedule, settings=None,
                                  markets=["KR", "US"], ctx=ctx)}["reflect"]  # fmt: skip
    assert job.cron["day_of_week"] == "mon-fri" and job.cron["hour"] == 16
    out = qj.on_cron_missed(deps, job, notify=False)
    c = deps.conn_factory()
    assert out["queued"] is True and jobq.list_recent(c)[0]["kind"] == "reflect"
    c.close()


def test_reflect_request_honors_market(project, settings, monkeypatch):
    seen = []
    monkeypatch.setattr(
        jobs, "pending", lambda conn, *, market=None, **kw: seen.append(market) or []
    )
    jobs.reflect_request(_deps(project, settings), {"market": "US"}, lambda s: None)
    assert seen == ["US"]
    assert jobs.ReflectParams().market is None
    with pytest.raises(ValueError):
        jobs.ReflectParams(market="KR", extra=1)


def test_reflect_module_exports(conn):
    assert reflect.pending(conn) == []
