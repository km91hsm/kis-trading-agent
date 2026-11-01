"""데몬 ↔ 세이브티커 수집 서비스: 앱이 켜져 있는 동안 서비스를 띄우고(계정 파일이 있을 때만),
죽으면 다시, 종료 때 정리. 장애 감시는 텔레그램, 보관 기간 정리는 운영일 1회."""

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from trader.features.saveticker import automation, job
from trader.features.saveticker.feature import FEATURE
from trader.kernel.cli.boot import build_registry
from trader.kernel.daemon.jobs import build
from trader.kernel.daemon.runner import DaemonDeps

NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


class FakeProc:
    def __init__(self):
        self.pid = 4242
        self.code = None

    def poll(self):
        return self.code


@pytest.fixture(autouse=True)
def _reset():
    job.reset_state()
    yield
    job.reset_state()


def _deps(project, *, stop=None):
    return DaemonDeps(
        settings=SimpleNamespace(),
        schedule=SimpleNamespace(),
        conn_factory=lambda: None,
        state=project / "state",
        now=lambda: NOW,
        markets=["KR"],
        gates={},
        stop_event=stop or threading.Event(),
        dry_run=False,
        notifier=Sent(),
        registry=build_registry([FEATURE]),
    )


def _patch(monkeypatch):
    spawned, monitored = [], []

    def spawn(argv, *, log, env):
        spawned.append((argv, log, env))
        return FakeProc()

    monkeypatch.setattr(job.procs, "spawn_detached", spawn)
    monkeypatch.setattr(automation, "service_elsewhere", lambda: False)  # 이 PC 의 실제 수집기 무시
    monkeypatch.setattr(
        automation,
        "monitor",
        lambda home, *, send: monitored.append(send) or {"health": "healthy", "faults": []},
    )
    return spawned, monitored


def test_job_registered(project):
    deps = _deps(project)
    jobs = build(
        [FEATURE], schedule=deps.schedule, settings=deps.settings, markets=["KR"], ctx=deps
    )
    (sj,) = jobs
    assert sj.id == "saveticker" and sj.spec.kind == "saveticker"
    assert sj.trigger_kwargs == {"seconds": 60}


def test_no_credentials_no_service(project, monkeypatch):
    spawned, monitored = _patch(monkeypatch)
    out = job.watch(_deps(project), None)
    assert out["service"] == "계정 파일 없음" and not spawned and not monitored


def test_starts_once_and_restarts_when_dead(project, monkeypatch):
    spawned, monitored = _patch(monkeypatch)
    (project / "secrets" / "saveticker.env").write_text("x", encoding="utf-8")
    deps = _deps(project)
    out = job.watch(deps, None)
    argv, log, env = spawned[0]
    assert argv[-2:] == ["saveticker", "service"] and env["TRADER_HOME"] == str(project)
    assert log == project / "state" / "external" / "saveticker" / "runtime" / "daemon-service.log"
    assert out["service"].startswith("시작") and monitored
    assert job.watch(deps, None)["service"] == "실행 중" and len(spawned) == 1
    job._STATE.proc.code = 1  # 죽었다
    job.watch(deps, None)
    assert len(spawned) == 2
    # 텔레그램 알림은 messages 한 곳의 문구로
    monitored[-1]("수집 장애: news_all")
    assert deps.notifier.msgs[-1].startswith("[세이브티커 수집]")


def test_disabled_by_config(project, monkeypatch):
    spawned, _ = _patch(monkeypatch)
    (project / "secrets" / "saveticker.env").write_text("x", encoding="utf-8")
    (project / "config" / "sources.yaml").write_text(
        "saveticker: {collect: false}\n", encoding="utf-8"
    )
    assert job.watch(_deps(project), None) == {"skipped": "꺼짐"}
    assert not spawned


def test_prune_once_per_day(project, monkeypatch):
    _patch(monkeypatch)
    calls = []
    monkeypatch.setattr(
        "trader.features.saveticker.store.Store.prune",
        lambda self, *, today, keep_days: calls.append((today, keep_days)) or {"revisions": 0},
    )
    (project / "state" / "external" / "saveticker" / "data").mkdir(parents=True)
    deps = _deps(project)
    job.watch(deps, None)
    job.watch(deps, None)
    assert calls == [(NOW.date(), 30)]


def test_shutdown_stops_service(project, monkeypatch):
    stopped = []
    monkeypatch.setattr(job.procs, "stop_tree", lambda proc, *, grace: stopped.append(grace))
    deps = _deps(project)
    job._STATE.proc = FakeProc()
    job.stop(deps)
    assert stopped == [automation.STOP_GRACE_S] and job._STATE.proc is None
    job.stop(deps)  # 없으면 아무 일 없음
    assert len(stopped) == 1


def test_stop_event_skips_start_and_teardown_runs_once(project, monkeypatch):
    spawned, monitored = _patch(monkeypatch)
    stopped = []
    monkeypatch.setattr(job.procs, "stop_tree", lambda proc, *, grace: stopped.append(grace))
    (project / "secrets" / "saveticker.env").write_text("x", encoding="utf-8")
    stop = threading.Event()
    deps = _deps(project, stop=stop)
    job.watch(deps, None)  # 서비스를 띄운다
    assert len(spawned) == 1
    job._STATE.proc.code = 1  # 죽었지만 종료 중이면 다시 띄우지 않는다
    stop.set()
    assert job.watch(deps, None) == {"skipped": "종료 중"} and len(spawned) == 1
    job.stop(deps)
    job.stop(deps)
    assert stopped == [automation.STOP_GRACE_S]
