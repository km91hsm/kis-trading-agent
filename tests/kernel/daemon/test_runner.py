"""runner: run_job 기록 규칙, DaemonDeps, Daemon 기동·종료·시그널."""

import json
import logging
import signal
import threading
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from trader.kernel.daemon import heartbeat as hb_mod
from trader.kernel.daemon.jobs import build
from trader.kernel.daemon.runner import Daemon, DaemonDeps, run_job
from trader.kernel.ledger import connect, init_schema
from trader.kernel.ledger.migrations import KERNEL
from trader.kernel.registry.types import Feature, JobSpec

NOW = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)


def spec(**kw) -> JobSpec:
    base = dict(
        id="j", kind="kind_a", trigger="cron", per_market=False, schedule_key="at",
        interval_s=None, enabled=None, gate="none", heavy=False, misfire="default",
        catch_up="none", start_soon_s=0, body=lambda ctx, job: {"n": 1},
    )  # fmt: skip
    base.update(kw)
    return JobSpec(**base)


def make_deps(project: Path, **kw) -> DaemonDeps:
    state = project / "state"
    db = state / "t.db"

    def factory():
        return connect(db)

    c = factory()
    init_schema(c, KERNEL)
    c.close()
    base = dict(
        settings=SimpleNamespace(), schedule=SimpleNamespace(at="03:00"), conn_factory=factory,
        state=state, now=lambda: NOW, markets=["KR"], gates={}, stop_event=threading.Event(),
        dry_run=False,
    )  # fmt: skip
    base.update(kw)
    return DaemonDeps(**base)


@pytest.fixture
def deps(project):
    return make_deps(project)


@pytest.fixture
def conn(deps):
    c = deps.conn_factory()
    yield c
    c.close()


def job_of(deps, **kw):
    feature = Feature(id="f", tier="lane", jobs=(spec(**kw),))
    (job,) = build(
        [feature], schedule=deps.schedule, settings=deps.settings, markets=deps.markets, ctx=deps
    )
    return job


def rows(conn):
    return conn.execute("SELECT kind, market, status, error, result_json FROM runs").fetchall()


def test_deps_is_frozen_with_exact_field_order(deps):
    assert [f.name for f in fields(DaemonDeps)] == [
        "settings", "schedule", "conn_factory", "state", "now", "markets", "gates", "stop_event",
        "dry_run", "notifier", "secrets", "registry", "hooks",
    ]  # fmt: skip
    with pytest.raises(FrozenInstanceError):
        deps.dry_run = True  # type: ignore[misc]


def test_run_job_cron_ok_records_row(deps, conn):
    out = run_job(conn, job_of(deps), deps, stop_event=deps.stop_event)
    assert out == {"n": 1}
    (r,) = rows(conn)
    assert (r["kind"], r["market"], r["status"]) == ("kind_a", None, "ok")
    assert json.loads(r["result_json"]) == {"n": 1}


def test_run_job_records_error_and_swallows(deps, conn):
    def boom(ctx, job):
        raise RuntimeError("터짐")

    out = run_job(conn, job_of(deps, body=boom), deps, stop_event=deps.stop_event)
    assert "터짐" in out["error"]
    (r,) = rows(conn)
    assert r["status"] == "error" and "RuntimeError: 터짐" in r["error"]


def test_run_job_body_error_key_marks_error(deps, conn):
    run_job(
        conn, job_of(deps, body=lambda c, j: {"error": "bad"}), deps, stop_event=deps.stop_event
    )
    (r,) = rows(conn)
    assert r["status"] == "error" and r["error"] == "bad"


def test_run_job_skipped_result_is_recorded_skipped(deps, conn):
    body = lambda c, j: {"skipped": "휴장"}  # noqa: E731
    out = run_job(conn, job_of(deps, body=body), deps, stop_event=deps.stop_event)
    assert out == {"skipped": "휴장"}
    (r,) = rows(conn)
    assert r["status"] == "skipped" and json.loads(r["result_json"]) == {"skipped": "휴장"}


def test_run_job_non_dict_result_is_wrapped(deps, conn):
    out = run_job(conn, job_of(deps, body=lambda c, j: 7), deps, stop_event=deps.stop_event)
    assert out == {"result": 7}
    assert rows(conn)[0]["status"] == "ok"


def test_run_job_interval_ok_writes_no_row(deps, conn):
    j = job_of(deps, trigger="interval", schedule_key=None, interval_s=30)
    assert run_job(conn, j, deps, stop_event=deps.stop_event) == {"n": 1}
    assert rows(conn) == []


def test_run_job_interval_error_writes_row(deps, conn):
    def boom(ctx, job):
        raise ValueError("x")

    j = job_of(deps, trigger="interval", schedule_key=None, interval_s=30, body=boom)
    run_job(conn, j, deps, stop_event=deps.stop_event)
    (r,) = rows(conn)
    assert r["status"] == "error" and "ValueError: x" in r["error"]


def test_run_job_interval_skipped_writes_no_row(deps, conn):
    j = job_of(deps, trigger="interval", schedule_key=None, interval_s=30,
               body=lambda c, jb: {"skipped": "x"})  # fmt: skip
    assert run_job(conn, j, deps, stop_event=deps.stop_event) == {"skipped": "x"}
    assert rows(conn) == []


def test_run_job_gate_reason_records_skipped_without_running_body(project):
    called = []
    d = make_deps(project, gates={"market": lambda job: "휴장일"})
    c = d.conn_factory()
    j = job_of(d, gate="market", body=lambda ctx, jb: called.append(1))
    out = run_job(c, j, d, stop_event=d.stop_event)
    assert out == {"skipped": "휴장일"} and called == []
    (r,) = rows(c)
    assert r["status"] == "skipped" and json.loads(r["result_json"]) == {"skipped": "휴장일"}
    c.close()


def test_run_job_raising_gate_is_recorded_as_error(project):
    def bad_gate(job):
        raise RuntimeError("gate boom")

    d = make_deps(project, gates={"market": bad_gate})
    c = d.conn_factory()
    out = run_job(c, job_of(d, gate="market"), d, stop_event=d.stop_event)
    assert "gate boom" in out["error"]
    (r,) = rows(c)
    assert r["status"] == "error" and "gate boom" in r["error"]
    c.close()


def test_tick_records_exception_in_heartbeat(project, monkeypatch):
    from trader.kernel.daemon import runner as runner_mod

    d = make_deps(project)
    job = job_of(d)
    daemon = Daemon([], d)
    seen = []
    monkeypatch.setattr(
        daemon,
        "_hb",
        SimpleNamespace(
            job_started=lambda i: None,
            job_finished=lambda i, s, e: seen.append((s, e)),
            set_next=lambda *a: None,
        ),
    )
    monkeypatch.setattr(daemon, "_sync_next", lambda i: None)
    monkeypatch.setattr(
        runner_mod, "run_job", lambda *a, **k: (_ for _ in ()).throw(ValueError("zz"))
    )
    with pytest.raises(ValueError):
        daemon._tick(job)
    assert seen[0][0] == "error" and "zz" in seen[0][1]


def test_run_job_gate_none_result_runs_body(project):
    d = make_deps(project, gates={"market": lambda job: None})
    c = d.conn_factory()
    run_job(c, job_of(d, gate="market"), d, stop_event=d.stop_event)
    assert rows(c)[0]["status"] == "ok"
    c.close()


def test_run_job_skips_when_stop_event_set(deps, conn):
    deps.stop_event.set()
    out = run_job(conn, job_of(deps), deps, stop_event=deps.stop_event)
    assert "skipped" in out and rows(conn) == []


def test_run_job_passes_ctx_and_job_to_body(deps, conn):
    seen = []
    j = job_of(deps, body=lambda ctx, jb: seen.append((ctx, jb)) or {})
    run_job(conn, j, deps, stop_event=deps.stop_event)
    assert seen == [(deps, j)]


# --- Daemon ---------------------------------------------------------------------


def make_daemon(deps, *specs):
    return Daemon([Feature(id="f", tier="lane", jobs=tuple(specs))], deps)


def test_daemon_start_registers_jobs_heartbeat_and_shutdown(project):
    d = make_deps(project)
    daemon = make_daemon(d, spec(id="a"), spec(id="p", trigger="interval", schedule_key=None,
                                               interval_s=3600))  # fmt: skip
    try:
        assert daemon.start(install_signals=False) is True
        ids = {j.id for j in daemon._scheduler.get_jobs()}
        assert ids == {"a", "p", "daemon_heartbeat"}
        data = json.loads((d.state / hb_mod.FILE_NAME).read_text(encoding="utf-8"))
        assert set(data["jobs"]) == {"a", "p"} and data["jobs"]["a"]["next_run"]
        assert (d.state / "daemon.lock").exists()
    finally:
        daemon.shutdown(wait=False)
    assert not (d.state / "daemon.lock").exists()
    assert "stopped_at" in json.loads((d.state / hb_mod.FILE_NAME).read_text(encoding="utf-8"))
    assert d.stop_event.is_set()
    daemon.shutdown(wait=False)  # 두 번째 호출은 무해


def test_daemon_second_start_fails_on_lock(project):
    d = make_deps(project)
    first = make_daemon(d, spec(id="a"))
    second = make_daemon(d, spec(id="a"))
    try:
        assert first.start(install_signals=False) is True
        assert second.start(install_signals=False) is False
        assert second._scheduler is None
        second.shutdown(wait=False)  # 못 잡은 인스턴스의 종료는 first 의 잠금을 건드리지 않는다
        assert (d.state / "daemon.lock").exists()
        assert first._lock.held() is True
    finally:
        first.shutdown(wait=False)
        second.shutdown(wait=False)


def test_tick_runs_job_and_updates_heartbeat(project):
    d = make_deps(project)
    daemon = make_daemon(d, spec(id="a"))
    try:
        assert daemon.start(install_signals=False) is True
        (job,) = daemon._jobs
        daemon._tick(job)
        c = d.conn_factory()
        assert rows(c)[0]["status"] == "ok"
        c.close()
        data = json.loads((d.state / hb_mod.FILE_NAME).read_text(encoding="utf-8"))
        assert data["jobs"]["a"]["last_status"] == "ok"
    finally:
        daemon.shutdown(wait=False)


def test_start_quiets_apscheduler_loggers(project):
    d = make_deps(project)
    daemon = make_daemon(d, spec(id="a"))
    logging.getLogger("apscheduler.scheduler").setLevel(logging.NOTSET)
    try:
        daemon.start(install_signals=False)
        assert logging.getLogger("apscheduler.scheduler").level == logging.WARNING
    finally:
        daemon.shutdown(wait=False)


def test_start_installs_and_shutdown_restores_signal_handlers(project):
    before = signal.getsignal(signal.SIGTERM)
    d = make_deps(project)
    daemon = make_daemon(d, spec(id="a"))
    try:
        daemon.start()
        assert signal.getsignal(signal.SIGTERM) == daemon._on_signal
    finally:
        daemon.shutdown(wait=False)
    assert signal.getsignal(signal.SIGTERM) == before


def test_second_sigterm_ignored_while_waiting(project):
    d = make_deps(project)
    daemon = make_daemon(d, spec(id="a"))
    calls = []

    class FakeScheduler:
        def shutdown(self, wait=True):
            calls.append(wait)
            daemon._on_signal(signal.SIGTERM, None)  # 대기 중 두 번째 시그널

        def get_jobs(self):
            return []

    try:
        assert daemon.start(install_signals=False) is True
        real = daemon._scheduler
        real.shutdown(wait=False)
        daemon._scheduler = FakeScheduler()
        daemon._on_signal(signal.SIGTERM, None)
        assert calls == [True]  # 첫 시그널만 shutdown(wait=True) 를 부른다
        assert d.stop_event.is_set()
    finally:
        daemon.shutdown(wait=False)


def test_start_failure_after_scheduler_start_cleans_up(project, monkeypatch):
    d = make_deps(project)
    daemon = make_daemon(d, spec(id="a"))

    def boom(*a, **k):
        raise RuntimeError("설치 실패")

    monkeypatch.setattr(daemon, "_install_signals", boom)
    created = []
    real_start = BackgroundScheduler.start

    def spy(self, *a, **k):
        created.append(self)
        return real_start(self, *a, **k)

    monkeypatch.setattr(BackgroundScheduler, "start", spy)
    with pytest.raises(RuntimeError, match="설치 실패"):
        daemon.start()
    assert created and not created[0].running
    assert not (d.state / "daemon.lock").exists()
    assert daemon._lock.held() is False


def _teardown_daemon(project, teardown, markets=("KR", "US")):
    s = spec(id="j_{market}", per_market=True, schedule_key="at", teardown=teardown)
    d = Daemon([Feature(id="f", tier="lane", jobs=(s,))], make_deps(project, markets=list(markets)))
    d._jobs = build(
        d.features, schedule=d.deps.schedule, settings=d.deps.settings,
        markets=d.deps.markets, ctx=d.deps,
    )  # fmt: skip
    return d


def test_shutdown_calls_teardown_once_per_spec(project):
    calls = []
    d = _teardown_daemon(project, lambda ctx: calls.append(ctx))
    assert len(d._jobs) == 2
    d.shutdown()
    d.shutdown()
    assert calls == [d.deps]


def test_shutdown_continues_when_teardown_raises(project):
    calls = []

    def boom(ctx):
        raise RuntimeError("터짐")

    s1 = spec(id="a", teardown=boom)
    s2 = spec(id="b", teardown=lambda ctx: calls.append("b"))
    d = Daemon([Feature(id="f", tier="lane", jobs=(s1, s2))], make_deps(project))
    d._jobs = build(
        d.features, schedule=d.deps.schedule, settings=d.deps.settings,
        markets=d.deps.markets, ctx=d.deps,
    )  # fmt: skip
    d.shutdown()
    assert calls == ["b"]
