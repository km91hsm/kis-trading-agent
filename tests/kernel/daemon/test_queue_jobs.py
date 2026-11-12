"""큐 틱 4레인·heavy 락·재기동 복구·놓친 cron 따라잡기.

옛 tests/test_daemon_queue.py 25건을 이식했다. 실제 kind(scan_analyze·fills_sync·reevaluate …)
대신 이 파일의 합성 `QueueKind`·`JobSpec` 을 쓴다(커널은 기능 이름을 모른다):

- heavy_scan(slow, trading) ← scan_analyze, train/train2(slow) ← ml_train/rl_train,
  backfill(slow, progress) ← features_backfill, sync(fast, restart_requeue) ← fills_sync,
  collect(fast) ← news_collect, reeval(reeval, trading, priority 10) ← reevaluate,
  probe(info) ← usage_probe, reflect_q(slow) ← reflect.
- cron: `{market}_scan`(heavy·misfire scan·catch_up queue), `reflect_q`(idempotent·queue),
  `backup`·`members`(idempotent·direct), interval `{market}_watch`(default·none).
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from apscheduler import events as ev
from pydantic import BaseModel, ConfigDict

from trader.kernel.cli.boot import build_registry
from trader.kernel.config import LlmSettings
from trader.kernel.config.loader import load
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon import queue_jobs as qj
from trader.kernel.daemon.gates import should_skip
from trader.kernel.daemon.heartbeat import Heartbeat
from trader.kernel.daemon.jobs import build
from trader.kernel.daemon.runner import Daemon, DaemonDeps, _DropMaxInstances, run_job
from trader.kernel.ledger import connect, init_schema
from trader.kernel.platform.bundle import templates_dir
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations
from trader.kernel.registry.types import Feature, JobSpec, QueueKind
from trader.kernel.session.run_store import run_date_for

NOW = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)  # 토요일
TUE_0900_KST = datetime(2026, 10, 27, 0, 0, tzinfo=UTC)  # 평일 장 시작 전후(마감 전)


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarketParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str
    ticker: str | None = None
    trigger_id: int | None = None


class Box:
    """kind 본체를 테스트마다 바꿔 끼운다. 기본 본체는 kind 이름을 ran 에 남긴다."""

    def __init__(self) -> None:
        self.ran: list[str] = []
        self.bodies: dict[str, Callable] = {}

    def body(self, kind: str):
        def run(deps, params, progress):
            fn = self.bodies.get(kind)
            if fn is not None:
                return fn(deps, params, progress)
            self.ran.append(kind)
            return {}

        return run


def make_kinds(box: Box) -> dict[str, QueueKind]:
    rows = [
        # kind, params, lane, priority, trading, restart_requeue
        ("heavy_scan", MarketParams, "slow", 0, True, False),
        ("train", NoParams, "slow", 0, False, False),
        ("train2", NoParams, "slow", 0, False, False),
        ("backfill", NoParams, "slow", 0, False, False),
        ("reflect_q", NoParams, "slow", 0, False, False),
        ("sync", NoParams, "fast", 0, False, True),
        ("collect", NoParams, "fast", 0, False, False),
        ("reeval", MarketParams, "reeval", 10, True, False),
        ("reeval_rr", MarketParams, "reeval", 10, True, True),
        ("probe", NoParams, "info", 0, False, False),
    ]
    return {
        k: QueueKind(
            kind=k, params_model=pm, label=f"{k} 작업", lane=lane, priority=prio,
            trading=trading, restart_requeue=rr, body=box.body(k),
        )  # fmt: skip
        for k, pm, lane, prio, trading, rr in rows
    }


def spec(**kw) -> JobSpec:
    base = dict(
        id="j", kind="j", trigger="cron", per_market=False, schedule_key=None, interval_s=None,
        enabled=None, gate="none", heavy=False, misfire="default", catch_up="none",
        start_soon_s=0, body=lambda ctx, job: {},
    )  # fmt: skip
    base.update(kw)
    return JobSpec(**base)


def make_feature(order: list[str]) -> Feature:
    return Feature(
        id="synthetic", tier="lane",
        jobs=(
            spec(id="{market}_scan", kind="heavy_scan", per_market=True,
                 schedule_key="{market}.scan", gate="market", heavy=True, misfire="scan",
                 catch_up="queue", body=lambda ctx, job: order.append("cron") or {}),
            spec(id="{market}_watch", kind="watch", trigger="interval", per_market=True,
                 interval_s=60, gate="market"),
            spec(id="reflect_q", kind="reflect_q", schedule_key="reflect", misfire="idempotent",
                 catch_up="queue"),
            spec(id="backup", kind="backup", schedule_key="backup", misfire="idempotent",
                 catch_up="direct", body=lambda ctx, job: order.append("backup") or {}),
            spec(id="members", kind="members", schedule_key="members", misfire="idempotent",
                 catch_up="direct", body=lambda ctx, job: order.append("members") or {}),
        ),
    )  # fmt: skip


class Sent:
    def __init__(self) -> None:
        self.msgs: list[str] = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


class Clock:
    def __init__(self, now: datetime) -> None:
        self.t = now

    def __call__(self) -> datetime:
        return self.t


def market_gate(deps, holidays=frozenset()):
    """accounts market 게이트의 합성판: 주말·휴장(일시정지 없음), 시장 현지 운영일 기준."""

    def gate(job):
        if job.market is None:
            return None
        today = run_date_for(job.market, deps.now())
        return should_skip(
            job.market, today, paused=lambda: False, is_holiday=lambda d: d in holidays
        )

    return gate


SCHEDULE = SimpleNamespace(
    kr=SimpleNamespace(scan="07:30"),
    us=SimpleNamespace(scan=SimpleNamespace(time="08:30", tz="America/New_York")),
    reflect="16:30",
    backup="17:10",
    ledger_backup="17:10",
    retention="17:20",
    members="17:20",
)


class Env:
    def __init__(self, deps: DaemonDeps, box: Box, order: list[str]) -> None:
        self.deps, self.box, self.order = deps, box, order
        self.feature = make_feature(order)

    @property
    def ran(self) -> list[str]:
        return self.box.ran

    def jobs(self, markets=None):
        return build(
            [self.feature, qj.QUEUE_FEATURE], schedule=self.deps.schedule,
            settings=self.deps.settings, markets=markets or self.deps.markets, ctx=self.deps,
        )  # fmt: skip

    def job(self, ident: str, markets=None):
        return next(j for j in self.jobs(markets) if j.id == ident)

    def hb(self) -> Heartbeat:
        hb = Heartbeat(self.deps.state, self.deps.now)
        hb.start(pid=1, jobs=self.jobs())
        self.deps.queue.heartbeat = hb
        return hb

    def tick(self, *lanes: str) -> dict:
        return qj.queue_tick(self.deps, lanes=frozenset(lanes))


def make_env(project: Path, now: datetime, *, markets=("KR",), holidays=frozenset()) -> Env:
    db = project / "state" / "t.db"

    def factory():
        return connect(db)

    c = factory()
    init_schema(c, all_migrations(discover()))
    c.close()
    box, order = Box(), []
    deps = DaemonDeps(
        settings=SimpleNamespace(llm=LlmSettings()), schedule=SCHEDULE, conn_factory=factory,
        state=project / "state", now=Clock(now), markets=list(markets), gates={},
        stop_event=threading.Event(), dry_run=False, notifier=Sent(),
        queue=qj.QueueState(kinds=make_kinds(box)),
    )  # fmt: skip
    deps.gates["market"] = market_gate(deps, holidays)
    return Env(deps, box, order)


@pytest.fixture
def env(project):
    return make_env(project, NOW)


@pytest.fixture
def conn(env):
    c = env.deps.conn_factory()
    yield c
    c.close()


def enqueue(env, conn, kind, params, *, source="web", now=NOW):
    return jobq.enqueue(conn, env.deps.queue.kinds, kind, params, source=source, now=now)


def run_in_thread(env, job) -> threading.Thread:
    def target():
        c = env.deps.conn_factory()
        try:
            run_job(c, job, env.deps, stop_event=env.deps.stop_event)
        finally:
            c.close()

    t = threading.Thread(target=target)
    t.start()
    return t


def test_build_jobs_includes_job_queue_interval(env):
    jobs = {j.id: j for j in env.jobs()}
    assert jobs["job_queue"].kind == "job_queue" and jobs["job_queue"].interval_s == 10
    assert {"job_queue", "job_queue_fast", "job_queue_reeval", "job_queue_info"} <= set(jobs)
    sched = SimpleNamespace(job_queue=SimpleNamespace(interval_s=5))
    (q, *_) = build([qj.QUEUE_FEATURE], schedule=sched, settings=None, markets=[], ctx=env.deps)
    assert q.interval_s == 5 and q.spec.schedule_key == "job_queue.interval_s"
    # 커널 schedule 섹션 job_queue.interval_s(템플릿 기본 10)
    path = templates_dir() / "config" / "schedule.yaml"
    tmpl = load("schedule", build_registry(discover()), path=path)
    (q, *_) = build([qj.QUEUE_FEATURE], schedule=tmpl, settings=None, markets=[], ctx=env.deps)
    assert tmpl.job_queue.interval_s == 10 and q.interval_s == 10


def test_queue_tick_runs_request_and_records_runs_and_heartbeat(env, conn):
    env.box.bodies["heavy_scan"] = lambda d, p, prog: {"targets": 0, "m": p["market"]}
    rid = enqueue(env, conn, "heavy_scan", {"market": "KR"})
    hb = env.hb()
    assert env.tick("slow") == {"request": rid, "kind": "heavy_scan"}
    req = jobq.list_recent(conn)[0]
    assert req["status"] == "ok" and req["result"] == {"targets": 0, "m": "KR"}
    runs = conn.execute("SELECT kind, market, status FROM runs").fetchall()
    assert [(r["kind"], r["market"], r["status"]) for r in runs] == [("heavy_scan", "KR", "ok")]
    assert hb._data["jobs"]["kr_heavy_scan"]["last_status"] == "ok"
    assert env.tick("slow") == {"skipped": "대기 없음"}


def test_queue_tick_marks_error_and_runs_row_on_exception(env, conn):
    def boom(d, p, prog):
        raise RuntimeError("폭발")

    env.box.bodies["train"] = boom
    rid = enqueue(env, conn, "train", {})
    r = env.tick("slow")  # 예외는 삼켜진다
    assert r == {"request": rid, "kind": "train"}
    req = jobq.list_recent(conn)[0]
    assert req["status"] == "error" and "폭발" in req["error"]
    run = conn.execute("SELECT kind, status FROM runs").fetchone()
    assert run["kind"] == "train" and run["status"] == "error"


def test_queue_tick_skips_while_heavy_lock_held(env, conn):
    enqueue(env, conn, "train", {})
    with env.deps.queue.heavy_lock:
        assert env.tick("slow") == {"skipped": "다른 무거운 잡 실행 중"}
    assert jobq.list_recent(conn)[0]["status"] == "queued"
    env.deps.stop_event.set()
    assert env.tick("slow") == {"skipped": "종료 중"}


def test_cron_heavy_job_waits_for_heavy_lock(project, monkeypatch):
    """합성 heavy cron(옛 scan_analyze 자리)은 heavy 락을 기다렸다 돈다."""
    env = make_env(project, datetime(2026, 10, 8, 3, 0, tzinfo=UTC))
    monkeypatch.setattr(qj, "HEAVY_POLL_S", 0.05)
    job = env.job("kr_scan")
    env.deps.queue.heavy_lock.acquire()
    t = run_in_thread(env, job)
    t.join(0.2)
    assert t.is_alive() and env.order == []  # 락을 기다린다
    env.order.append("queue-done")
    env.deps.queue.heavy_lock.release()
    t.join(2)
    assert env.order == ["queue-done", "cron"]


@pytest.mark.parametrize("kind,requeued", [("sync", True), ("train", False)])
def test_bootstrap_queue(env, conn, kind, requeued):
    """재기동: running 은 error 로 닫고 restart_requeue kind 만 같은 params 로 다시 넣는다."""
    rid = enqueue(env, conn, kind, {})
    jobq.claim_next(conn, now=NOW, kinds=[kind])
    r = qj.bootstrap_queue(env.deps)
    assert r["stale"] == 1
    assert r["requeued"] == ([f"{kind}#{rid}"] if requeued else [])
    queued = [(x["kind"], x["source"]) for x in jobq.list_recent(conn) if x["status"] == "queued"]
    assert queued == ([(kind, "restart")] if requeued else [])


def test_run_marks_stale_running_and_expires_old_queued(env, conn):
    old = NOW - timedelta(hours=7)
    a = enqueue(env, conn, "train", {}, now=old)
    jobq.claim_next(conn, now=old, kinds=["train"])
    b = enqueue(env, conn, "train2", {}, now=old)
    d = Daemon([env.feature], env.deps)
    try:
        assert d.start(install_signals=False) is True
    finally:
        d.shutdown(wait=False)
    by = {r["id"]: r["status"] for r in jobq.list_recent(conn)}
    assert by[a] == "error" and by[b] == "skipped"


def test_listener_ignores_max_instances_for_job_queue(env):
    hb = env.hb()
    by_id = {j.id: j for j in env.jobs()}
    qj.on_scheduler_event(
        env.deps, hb, by_id, SimpleNamespace(code=ev.EVENT_JOB_MAX_INSTANCES, job_id="job_queue")
    )
    assert hb._data["jobs"]["job_queue"]["last_status"] is None
    qj.on_scheduler_event(
        env.deps, hb, by_id, SimpleNamespace(code=ev.EVENT_JOB_MAX_INSTANCES, job_id="kr_scan")
    )
    assert hb._data["jobs"]["kr_scan"]["last_status"] == "missed"
    # 큐 잡의 max_instances 경고 로그도 버린다
    rec = logging.LogRecord(
        "apscheduler", logging.WARNING, __file__, 1,
        'Execution of job "job_queue (trigger: interval[0:00:10])" skipped: '
        "maximum number of running instances reached (1)", None, None,
    )  # fmt: skip
    assert _DropMaxInstances().filter(rec) is False


def test_missed_cron_notifies_and_enqueues_catch_up(project):
    env = make_env(project, TUE_0900_KST)
    conn = env.deps.conn_factory()
    job = env.job("kr_scan")
    assert qj.misfire_grace_for(job.spec) == qj.SCAN_MISFIRE_GRACE_S
    out = qj.on_cron_missed(env.deps, job)
    msgs = env.deps.notifier.msgs
    assert out["queued"] is True and any("[잡 놓침] kr_scan" in m for m in msgs)
    assert jobq.list_recent(conn)[0]["kind"] == "heavy_scan"
    assert qj.on_cron_missed(env.deps, job)["queued"] is False  # 중복은 등록하지 않음
    watch = env.job("kr_watch")
    assert qj.misfire_grace_for(watch.spec) == qj.DEFAULT_MISFIRE_GRACE_S
    conn.close()


def test_heavy_wait_notifies_once_after_threshold(env, monkeypatch):
    sent: list[str] = []
    deps = replace(env.deps, notifier=SimpleNamespace(send=lambda t, **kw: sent.append(t)))
    monkeypatch.setattr(qj, "HEAVY_POLL_S", 0.02)
    monkeypatch.setattr(qj, "HEAVY_WAIT_NOTIFY_S", 0.04)
    deps.queue.heavy_lock.acquire()
    t = threading.Thread(target=lambda: qj.heavy(deps, lambda: 1))
    t.start()
    t.join(0.3)
    deps.queue.heavy_lock.release()
    t.join(2)
    assert len([m for m in sent if "[잡 대기]" in m]) == 1


def test_fast_lane_runs_while_heavy_lock_held(env, conn):
    """heavy 락을 누가 쥐고 있어도 빠른 레인 kind 는 빠른 레인이 꺼낸다."""
    enqueue(env, conn, "train", {})
    rid = enqueue(env, conn, "sync", {})
    with env.deps.queue.heavy_lock:
        assert env.tick("slow") == {"skipped": "다른 무거운 잡 실행 중"}
        assert env.tick("fast") == {"request": rid, "kind": "sync"}
        # 느린 레인 kind 는 빠른 레인이 가져가지 않는다
        assert env.tick("fast") == {"skipped": "대기 없음"}
    assert env.ran == ["sync"]
    assert env.tick("slow")["kind"] == "train"


def test_claim_next_filters_kinds(env, conn):
    kinds = env.deps.queue.kinds
    enqueue(env, conn, "train", {})
    fast = qj.lane_kinds(kinds, frozenset({"fast"}))
    reeval = qj.lane_kinds(kinds, frozenset({"reeval"}))
    assert jobq.claim_next(conn, now=NOW, kinds=fast) is None
    assert jobq.claim_next(conn, now=NOW, kinds=frozenset()) is None
    assert jobq.claim_next(conn, now=NOW, kinds=kinds)["kind"] == "train"
    assert "reeval" in reeval and "reeval" not in fast
    assert "backfill" not in fast | reeval


def test_heavy_wanted_counts_waiting_crons():
    """cron 둘이 기다릴 때 먼저 끝난 쪽이 양보 요청을 지우지 않는다."""
    w = qj.HeavyWanted()
    assert not w.is_set()
    w.enter()
    w.enter()
    w.leave()
    assert w.is_set()
    w.leave()
    w.leave()  # 0 아래로 내려가지 않는다
    assert not w.is_set()
    w.enter()
    assert w.is_set()


def test_main_queue_does_not_take_fast_kinds(env, conn):
    """두 레인이 같은 요청을 함께 꺼내지 않도록 느린 레인은 다른 레인 kind 를 건너뛴다."""
    enqueue(env, conn, "sync", {})
    assert env.tick("slow") == {"skipped": "대기 없음"}
    kinds = env.deps.queue.kinds
    lanes = [qj.lane_kinds(kinds, frozenset({ln})) for ln in ("slow", "fast", "info", "reeval")]
    for i, a in enumerate(lanes):
        for b in lanes[i + 1 :]:
            assert a.isdisjoint(b)
    assert frozenset().union(*lanes) == frozenset(kinds)
    assert sorted(ln for spec_lanes in qj.QUEUE_LANES.values() for ln in spec_lanes) == sorted(
        ["slow", "fast", "info", "reeval"]
    )


def test_reevaluate_lane_is_separate_from_light_jobs(env, conn):
    """재평가 레인(`job_queue_reeval`)은 빠른 레인이 가벼운 잡으로 막혀도 밀리지 않는다."""
    enqueue(env, conn, "collect", {})
    p = {"market": "KR", "ticker": "005930", "trigger_id": 1}
    rid = enqueue(env, conn, "reeval", p, source="monitor")
    assert env.tick("reeval") == {"request": rid, "kind": "reeval"}
    assert env.tick("reeval") == {"skipped": "대기 없음"}
    assert env.tick("fast")["kind"] == "collect"
    assert env.ran == ["reeval", "collect"]
    ids = {j.id for j in env.jobs()}
    assert {"job_queue_reeval", "job_queue_fast", "job_queue_info"} <= ids


def test_missed_cron_on_weekend_or_holiday_is_silent(project):
    env = make_env(project, NOW)  # NOW 는 토요일
    out = qj.on_cron_missed(env.deps, env.job("kr_scan"))
    assert out["skipped"] == "주말" and env.deps.notifier.msgs == []
    conn = env.deps.conn_factory()
    assert jobq.list_recent(conn) == []
    conn.close()
    hol = make_env(project, TUE_0900_KST, holidays=frozenset({TUE_0900_KST.date()}))
    assert qj.on_cron_missed(hol.deps, hol.job("kr_scan"))["skipped"] == "휴장일"
    assert hol.deps.notifier.msgs == []


def test_missed_trading_cron_after_close_alerts_without_catch_up(project):
    after = datetime(2026, 10, 27, 7, 0, tzinfo=UTC)  # 16:00 KST, 마감 뒤
    env = make_env(project, after)
    out = qj.on_cron_missed(env.deps, env.job("kr_scan"))
    assert out["queued"] is False and "late" in out and env.deps.notifier.msgs
    conn = env.deps.conn_factory()
    assert jobq.list_recent(conn) == []
    conn.close()


def test_catch_up_missed_on_restart_groups_alert_and_queues(project):
    """데몬이 07:30 에 꺼져 있다가 09:00 에 켜지면: 따라잡기 등록 + 놓친 잡 알림 1건."""
    now = TUE_0900_KST
    env = make_env(project, now)
    jobs = env.jobs()
    out = qj.catch_up_missed(env.deps, jobs)
    assert "kr_scan" in out["missed"] and "kr_scan" in out["queued"]
    assert len([m for m in env.deps.notifier.msgs if "잡 놓침" in m]) == 1
    # 이미 돈 잡은 놓친 것이 아니다
    conn = env.deps.conn_factory()
    conn.execute(
        "INSERT INTO runs(kind, market, started_at, status) VALUES (?,?,?,?)",
        ("heavy_scan", "KR", now.isoformat(), "ok"),
    )
    conn.commit()
    conn.close()
    assert "kr_scan" not in qj.catch_up_missed(env.deps, jobs)["missed"]


def test_auto_request_after_close_is_skipped_but_manual_runs(project):
    after = datetime(2026, 10, 27, 7, 0, tzinfo=UTC)  # 16:00 KST
    env = make_env(project, after)
    req = {"kind": "reeval", "params": {"market": "KR", "ticker": "005930"}, "source": "daemon"}
    assert qj.auto_gate(env.deps, req) == "장 마감"
    assert qj.auto_gate(env.deps, {**req, "source": "web"}) is None
    assert qj.auto_gate(env.deps, {**req, "kind": "sync", "params": {}}) is None
    pre = make_env(project, TUE_0900_KST)
    assert qj.auto_gate(pre.deps, {**req, "kind": "heavy_scan"}) is None
    # 꺼낼 때 다시 보고 skipped 로 닫는다(본체는 돌지 않음)
    conn = env.deps.conn_factory()
    rid = enqueue(env, conn, "reeval", req["params"], source="daemon", now=after)
    assert env.tick("reeval") == {"request": rid, "kind": "reeval"}
    row = jobq.list_recent(conn)[0]
    assert row["status"] == "skipped" and row["result"] == {"skipped": "장 마감"}
    assert env.ran == []
    conn.close()


def test_bootstrap_alerts_interrupted_reevaluate_without_requeue(project):
    """Review Focus 5: 끊긴 trading kind(재평가)는 주문을 이미 냈을 수 있어 재등록 없이 알린다."""
    env = make_env(project, TUE_0900_KST)
    conn = env.deps.conn_factory()
    rid = enqueue(
        env, conn, "reeval", {"market": "KR", "ticker": "005930", "trigger_id": 1},
        source="daemon", now=TUE_0900_KST,
    )  # fmt: skip
    jobq.claim_next(conn, now=TUE_0900_KST, kinds=["reeval"])  # 실행 중에 데몬이 죽었다
    out = qj.bootstrap_queue(env.deps)
    assert out["stale"] == 1
    kinds = [r["kind"] for r in jobq.list_recent(conn) if r["status"] == "queued"]
    assert kinds == []
    msgs = [m for m in env.deps.notifier.msgs if "작업 중단" in m]
    assert len(msgs) == 1 and f"#{rid}" in msgs[0]
    conn.close()


def test_queue_tick_yields_while_cron_waits(env):
    env.deps.queue.heavy_wanted.enter()
    try:
        assert env.tick("slow") == {"skipped": "예정 잡이 기다리는 중"}
    finally:
        env.deps.queue.heavy_wanted.leave()


def test_catch_up_counts_only_completed_runs_and_today(project):
    now = TUE_0900_KST
    env = make_env(project, now, markets=("KR", "US"))
    conn = env.deps.conn_factory()
    # 시작 직후 죽었다(running) → 놓친 것으로 다시
    conn.execute(
        "INSERT INTO runs(kind, market, started_at, status) VALUES (?,?,?,?)",
        ("heavy_scan", "KR", now.isoformat(), "running"),
    )
    conn.commit()
    conn.close()
    out = qj.catch_up_missed(env.deps, env.jobs())
    assert "kr_scan" in out["missed"]
    # 09:00 KST 는 뉴욕 월요일 20:00 — 월요일 US 잡(08:30 ET)은 그 운영일 안이라 놓친 것,
    # 하지만 장이 끝났으니 따라잡기 등록은 없다
    assert "us_scan" not in out["queued"]


def test_missed_idempotent_cron_jobs_catch_up(project):
    """놓친 멱등 cron: catch_up=queue 는 대기열로, catch_up=direct 는 그 자리에서 실행."""
    env = make_env(project, TUE_0900_KST)
    reflect = env.job("reflect_q")
    assert qj.misfire_grace_for(reflect.spec) == qj.IDEMPOTENT_MISFIRE_GRACE_S
    assert qj.misfire_grace_for(env.job("backup").spec) == qj.IDEMPOTENT_MISFIRE_GRACE_S
    out = qj.on_cron_missed(env.deps, reflect, notify=False)
    conn = env.deps.conn_factory()
    assert out["queued"] is True and jobq.list_recent(conn)[0]["kind"] == "reflect_q"
    conn.close()
    for ident in ("backup", "members"):
        out = qj.on_cron_missed(env.deps, env.job(ident), notify=False)
        assert out["ran"] is True
    assert env.order == ["backup", "members"]


def test_queued_heavy_yields_lock_to_waiting_cron(project, monkeypatch):
    """느린 레인 요청(옛 백필)이 heavy 락을 들고 있어도 cron 이 기다리면 progress 경계에서 락을
    내놓고 cron 이 끝난 뒤 이어서 돈다."""
    env = make_env(project, datetime(2026, 10, 8, 3, 0, tzinfo=UTC))
    monkeypatch.setattr(qj, "HEAVY_POLL_S", 0.05)
    cron_started = threading.Event()
    progress_seen = threading.Event()

    def backfill(d, p, progress):
        env.order.append("bf-start")
        progress_seen.set()
        cron_started.wait(2)  # cron 이 락을 기다리기 시작할 때까지
        progress("KR 005930: 10행")  # 여기서 양보 → cron 이 먼저 끝난다
        env.order.append("bf-end")
        return {"rows": 1}

    env.box.bodies["backfill"] = backfill
    conn = env.deps.conn_factory()
    enqueue(env, conn, "backfill", {})
    t_q = threading.Thread(target=env.tick, args=("slow",))
    t_q.start()
    assert progress_seen.wait(2)
    t_c = run_in_thread(env, env.job("kr_scan"))
    q = env.deps.queue
    deadline = time.monotonic() + 2
    while not q.heavy_wanted.is_set() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert t_c.is_alive() and q.heavy_wanted.is_set()
    cron_started.set()
    t_c.join(3)
    t_q.join(3)
    assert env.order == ["bf-start", "cron", "bf-end"]
    assert not q.heavy_lock.locked() and not q.heavy_wanted.is_set()
    assert jobq.list_recent(conn)[0]["status"] == "ok"
    conn.close()


def test_run_job_records_error_when_body_returns_error_dict(env, conn):
    job = build(
        [Feature(id="x", tier="lane", jobs=(spec(id="kr_analyze", kind="analyze",
              schedule_key="reflect", body=lambda c, j: {"targets": 0, "error": "_scan 없음"}),))],
        schedule=env.deps.schedule, settings=None, markets=[], ctx=env.deps,
    )[0]  # fmt: skip
    r = run_job(conn, job, env.deps, stop_event=env.deps.stop_event)
    assert r["error"] == "_scan 없음"
    row = conn.execute("SELECT status, error FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["status"] == "error" and "_scan" in row["error"]


def test_daemon_registers_queue_jobs_grace_and_start_soon(env):
    """Daemon.start: 큐 잡 4개 등록, misfire grace 는 spec.misfire, start_soon_s 는 첫 실행."""
    feature = Feature(
        id="soon", tier="lane",
        jobs=(spec(id="soon", kind="soon", trigger="interval", interval_s=1800, start_soon_s=60),),
    )  # fmt: skip
    real_now = datetime.now(UTC)  # 스케줄러는 벽시계로 돈다 — 첫 실행 시각을 지금 기준으로 본다
    env.deps.now.t = real_now
    d = Daemon([env.feature, feature], env.deps)
    try:
        assert d.start(install_signals=False) is True
        sched = d._scheduler
        ids = {j.id for j in sched.get_jobs()}
        assert set(qj.QUEUE_LANES) <= ids
        assert sched.get_job("kr_scan").misfire_grace_time == qj.SCAN_MISFIRE_GRACE_S
        assert sched.get_job("reflect_q").misfire_grace_time == qj.IDEMPOTENT_MISFIRE_GRACE_S
        first = sched.get_job("soon").next_run_time
        assert abs((first - (real_now + timedelta(seconds=60))).total_seconds()) < 1
        assert d.deps.queue.heartbeat is d._hb
    finally:
        d.shutdown(wait=False)


def test_restart_requeued_trading_request_rechecks_market_gate(env, conn):
    """Ruling 9: 재기동으로 다시 넣은 요청(source restart)도 자동 출처 — trading kind 는 꺼낼 때
    시장 게이트를 다시 보고, 걸리면 본체 없이 skipped 로 닫는다."""
    enqueue(env, conn, "reeval_rr", {"market": "KR"}, source="monitor")
    jobq.claim_next(conn, now=NOW, kinds=["reeval_rr"])  # 실행 중에 데몬이 죽었다
    r = qj.bootstrap_queue(env.deps)
    (row,) = [x for x in jobq.list_recent(conn) if x["status"] == "queued"]
    assert r["requeued"] and row["source"] == "restart"
    assert env.tick("reeval") == {"request": row["id"], "kind": "reeval_rr"}  # NOW 는 토요일
    done = next(x for x in jobq.list_recent(conn) if x["id"] == row["id"])
    assert done["status"] == "skipped" and done["result"] == {"skipped": "주말"}
    assert env.ran == []


def test_tick_records_error_when_run_job_fails_before_body(env, conn, monkeypatch):
    """run_job 이 본체 전에 실패하면(runs 기록 실패 등) 요청도 error — skipped 가 아니다."""
    from trader.kernel.daemon import runner

    rid = enqueue(env, conn, "train", {})
    hb = env.hb()
    calls = {"n": 0}
    real_start = runner.runs.start

    def flaky_start(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return real_start(*a, **kw)

    monkeypatch.setattr(runner.runs, "start", flaky_start)
    assert env.tick("slow") == {"request": rid, "kind": "train"}
    req = jobq.list_recent(conn)[0]
    assert req["status"] == "error" and "database is locked" in req["error"]
    assert env.ran == []
    assert hb._data["jobs"]["train"]["last_status"] == "error"


def test_tick_conn_factory_failure_does_not_leave_request_running(env, conn):
    """claim 뒤 연결이 실패해도 꺼낸 요청은 error 로 닫히고 하트비트가 running 으로 남지 않는다."""
    rid = enqueue(env, conn, "train", {})
    hb = env.hb()
    real = env.deps.conn_factory
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:  # 1: claim, 2: run_job 용 연결
            raise OSError("연결 실패")
        return real()

    deps = replace(env.deps, conn_factory=flaky)
    with pytest.raises(OSError):
        qj.queue_tick(deps, lanes=frozenset({"slow"}))
    row = next(r for r in jobq.list_recent(conn) if r["id"] == rid)
    assert row["status"] == "error" and "연결 실패" in row["error"]
    assert hb._data["jobs"]["train"]["last_status"] == "error"


def test_finish_write_failure_does_not_leave_request_running(env, conn, monkeypatch):
    """S1: 본체 뒤 finish 기록이 한 번 실패해도 요청은 running 으로 남지 않는다 — EmptyParams
    kind(sync)는 남으면 재기동 전까지 같은 요청이 전부 DuplicateRequest 가 된다."""
    rid = enqueue(env, conn, "sync", {})
    real = jobq.finish
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return real(*a, **kw)

    monkeypatch.setattr(jobq, "finish", flaky)
    assert env.tick("fast") == {"request": rid, "kind": "sync"}
    row = next(r for r in jobq.list_recent(conn) if r["id"] == rid)
    assert row["status"] != "running" and calls["n"] == 2
    assert enqueue(env, conn, "sync", {}) != rid  # DuplicateRequest 가 아니다


def test_auto_gate_matches_source_prefix(project):
    """S3: 'notice:{env}' 같은 접두 출처도 자동 출처('notice')로 본다. ':' 없는 출처는 정확히."""
    after = datetime(2026, 10, 27, 7, 0, tzinfo=UTC)  # 16:00 KST, 장 마감 뒤
    env = make_env(project, after)
    req = {"kind": "reeval", "params": {"market": "KR"}, "source": "notice:paper"}
    assert qj.auto_gate(env.deps, req) == "장 마감"
    assert qj.auto_gate(env.deps, {**req, "source": "news:live"}) == "장 마감"
    assert qj.auto_gate(env.deps, {**req, "source": "web:paper"}) is None
    assert qj.auto_gate(env.deps, {**req, "source": "noticeboard"}) is None
    assert qj.auto_gate(env.deps, {**req, "source": "notice"}) == "장 마감"


def test_enqueue_from_job_passes_priority(env, conn):
    rid = qj.enqueue_from_job(env.deps, "train", {}, source="news", priority=5)
    assert rid is not None
    row = conn.execute("SELECT priority FROM job_requests WHERE id=?", (rid,)).fetchone()
    assert row[0] == 5


def test_gate_skipped_auto_requests_are_swept_and_alerted_once(project):
    """장 마감 뒤 꺼낸 자동 trading 요청이 게이트에 걸리면 같은 시장·같은 사유로 대기 중인 자동
    요청을 그 틱에서 함께 닫고 알림 한 건으로 묶는다. 사람 요청·다른 시장·trading 아닌 kind 는
    남긴다."""
    after = datetime(2026, 10, 27, 6, 31, tzinfo=UTC)  # 15:31 KST, 장 마감 뒤
    env = make_env(project, after, markets=("KR", "US"))
    conn = env.deps.conn_factory()
    first = enqueue(env, conn, "heavy_scan", {"market": "KR", "ticker": "1"}, source="news",
                    now=after)  # fmt: skip
    enqueue(env, conn, "heavy_scan", {"market": "KR", "ticker": "2"}, source="news", now=after)
    enqueue(env, conn, "reeval", {"market": "KR", "ticker": "3"}, source="daemon", now=after)
    manual = enqueue(env, conn, "heavy_scan", {"market": "KR", "ticker": "4"}, source="web",
                     now=after)  # fmt: skip
    us = enqueue(env, conn, "reeval", {"market": "US", "ticker": "5"}, source="news", now=after)
    other = enqueue(env, conn, "sync", {}, source="daemon", now=after)

    assert env.tick("slow") == {"request": first, "kind": "heavy_scan"}
    by_id = {r["id"]: r for r in jobq.list_recent(conn, limit=20)}
    skipped = [i for i, r in by_id.items() if r["status"] == "skipped"]
    assert sorted(skipped) == [first, first + 1, first + 2]
    assert all(by_id[i]["result"] == {"skipped": "장 마감"} for i in skipped)
    assert by_id[manual]["status"] == "queued" and by_id[other]["status"] == "queued"
    assert by_id[us]["status"] == "queued"  # 미국은 아직 장 전 — 게이트에 걸리지 않음
    assert env.ran == []
    (msg,) = env.deps.notifier.msgs
    assert "KR" in msg and "장 마감" in msg and "3건" in msg
    assert "heavy_scan 작업 2" in msg and "reeval 작업 1" in msg
    conn.close()


def test_gate_skip_alert_once_per_market_reason_day(project):
    """같은 날 같은 시장·사유로 나중에 또 버려지면 다시 알리지 않는다(폭주 방지)."""
    after = datetime(2026, 10, 27, 6, 31, tzinfo=UTC)
    env = make_env(project, after)
    conn = env.deps.conn_factory()
    enqueue(env, conn, "reeval", {"market": "KR", "ticker": "1"}, source="news", now=after)
    env.tick("reeval")
    enqueue(env, conn, "reeval", {"market": "KR", "ticker": "2"}, source="news", now=after)
    env.tick("reeval")
    assert len(env.deps.notifier.msgs) == 1
    conn.close()


def test_start_does_not_wait_for_direct_catch_up(project):
    """기동 때 놓친 direct 잡(국면 판정처럼 몇 분 걸릴 수 있다)을 기다리느라 스케줄러·하트비트
    시작이 밀리면 그동안 손절 감시가 없다 — 따라잡기는 스케줄러가 뜬 뒤 뒤에서 돈다."""
    env = make_env(project, datetime(2026, 10, 6, 9, 0, tzinfo=UTC))  # 화 18:00 KST: 17:10 지남
    entered, release = threading.Event(), threading.Event()

    def slow(ctx, job):
        entered.set()
        release.wait(10)
        env.order.append("backup-done")
        return {}

    feature = Feature(
        id="synthetic", tier="lane",
        jobs=(spec(id="backup", kind="backup", schedule_key="backup", misfire="idempotent",
                   catch_up="direct", body=slow),),
    )  # fmt: skip
    d = Daemon([feature], env.deps)
    started: list[bool] = []
    t = threading.Thread(target=lambda: started.append(d.start(install_signals=False)))
    t.start()
    try:
        t.join(5)
        assert not t.is_alive() and started == [True], "start() 가 따라잡기를 기다리고 있다"
        assert entered.wait(5)  # 따라잡기는 뒤에서 시작됐다
        assert env.order == []  # 아직 끝나지 않았는데 start() 는 돌아왔다
        assert (env.deps.state / "daemon_heartbeat.json").is_file()
    finally:
        release.set()
        t.join(10)
        d.shutdown(wait=True)
    assert env.order == ["backup-done"]


def test_start_closes_running_runs_left_by_dead_daemon(env, conn):
    """데몬이 비정상 종료되면 cron 실행 기록(`runs`)이 running 으로 영구히 남는다 — 잠금을 잡은
    새 데몬이 기동 때 error 로 닫는다(끝난 기록은 건드리지 않는다)."""
    conn.execute(
        "INSERT INTO runs(kind, market, started_at, status) VALUES ('regime','KR',?,'running')",
        ((NOW - timedelta(minutes=5)).isoformat(),),
    )
    conn.execute(
        "INSERT INTO runs(kind, market, started_at, finished_at, status) "
        "VALUES ('scan','KR',?,?,'ok')",
        (NOW.isoformat(), NOW.isoformat()),
    )
    conn.commit()
    d = Daemon([env.feature], env.deps)
    try:
        assert d.start(install_signals=False) is True
    finally:
        d.shutdown(wait=True)
    rows = conn.execute(
        "SELECT kind, status, error FROM runs WHERE kind IN ('regime','scan') ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("regime", "error", "데몬 재시작으로 중단"), ("scan", "ok", None),
    ]  # fmt: skip
