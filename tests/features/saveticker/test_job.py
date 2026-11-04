"""데몬 ↔ 세이브티커 수집 서비스: 앱이 켜져 있는 동안 서비스를 띄우고(계정 파일이 있을 때만),
죽으면 다시, 종료 때 정리. 장애 감시는 텔레그램, 보관 기간 정리는 운영일 1회. 웹 '다시 시작'
요청(control `saveticker_restart_requested`)은 이 잡이 소비한다(옛
`test_saveticker_extension_restart_uses_popup_instead_of_legacy_reset` 단언은 여기로)."""

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from trader.features.saveticker import automation, job
from trader.features.saveticker.feature import FEATURE
from trader.features.saveticker.store import atomic_json
from trader.kernel.cli.boot import build_registry
from trader.kernel.daemon.jobs import build
from trader.kernel.daemon.runner import DaemonDeps
from trader.kernel.ledger import control, db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

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
    ledger = project / "state" / "trader.db"
    c = db.connect(ledger)
    try:
        db.init_schema(c, all_migrations(discover()))
    finally:
        c.close()
    return DaemonDeps(
        settings=SimpleNamespace(),
        schedule=SimpleNamespace(),
        conn_factory=lambda: db.connect(ledger),
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


FLAG = "saveticker_restart_requested"
RUNTIME = ("state", "external", "saveticker", "runtime")


def _request_restart(deps):
    conn = deps.conn_factory()
    try:
        control.set_flag(conn, FLAG, {"at": NOW.isoformat(), "actor": "web"})
    finally:
        conn.close()


def _flag(deps):
    conn = deps.conn_factory()
    try:
        return control.get_flag(conn, FLAG)
    finally:
        conn.close()


def test_restart_request_clears_blocks_and_flag(project, monkeypatch):
    """웹 '다시 시작' 요청: 정지·작업 쉬기 기록을 지우고(옛 legacy reset) 플래그를 지운다 —
    같은 틱에 서비스가 다시 뜬다."""
    spawned, _ = _patch(monkeypatch)
    (project / "secrets" / "saveticker.env").write_text("x", encoding="utf-8")
    runtime = project.joinpath(*RUNTIME)
    atomic_json(runtime / "service.json", {"state": "blocked", "reason": "restart_limit",
                                           "at": NOW.isoformat()})  # fmt: skip
    deps = _deps(project)
    assert job.watch(deps, None)["service"] != "실행 중" and not spawned  # 정지 중이라 안 뜬다
    _request_restart(deps)
    out = job.watch(deps, None)
    assert out["restart"] == "정지 기록 지움" and _flag(deps) is None
    assert automation.read(runtime / "service.json")["state"] == "stopped"
    assert len(spawned) == 1 and out["service"].startswith("시작")
    assert "restart" not in job.watch(deps, None)  # 소비했으니 다음 틱엔 없음


def test_extension_restart_uses_popup_instead_of_legacy_reset(project, monkeypatch):
    """확장 백엔드는 Chrome 확장 아이콘에서 재개한다 — 옛 정지 기록 지우기를 하지 않고 안내만
    보낸 뒤 플래그를 지운다."""
    _patch(monkeypatch)
    cleared = []
    monkeypatch.setattr(automation, "clear_blocks", lambda home: cleared.append(home))
    atomic_json(project.joinpath(*RUNTIME) / "bridge-config.json", {"backend": "extension"})
    deps = _deps(project)
    _request_restart(deps)
    out = job.watch(deps, None)
    assert cleared == [] and _flag(deps) is None
    assert "Chrome" in out["restart"]
    assert any("Chrome" in m for m in deps.notifier.msgs)
