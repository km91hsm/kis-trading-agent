"""데몬 ↔ 세이브티커 확장 수집 감시: 확장이 등록돼 있을 때만 장애·복구를 텔레그램으로, 보관 기간
정리는 운영일 1회. 아무 프로세스도 띄우지 않는다. 웹 '다시 시작' 요청(control
`saveticker_restart_requested`)은 이 잡이 소비해 Chrome 확장 팝업 재개 안내만 보낸다."""

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


RUNTIME = ("state", "external", "saveticker", "runtime")
FLAG = "saveticker_restart_requested"


def _patch(monkeypatch):
    monitored = []
    monkeypatch.setattr(
        automation,
        "monitor",
        lambda home, *, send: monitored.append(send) or {"health": "healthy", "faults": []},
    )
    return monitored


def _register(project):
    atomic_json(project.joinpath(*RUNTIME) / "bridge-config.json", {"backend": "extension"})


def test_job_registered(project):
    deps = _deps(project)
    jobs = build(
        [FEATURE], schedule=deps.schedule, settings=deps.settings, markets=["KR"], ctx=deps
    )
    (sj,) = jobs
    assert sj.id == "saveticker" and sj.spec.kind == "saveticker"
    assert sj.trigger_kwargs == {"seconds": 60}
    assert sj.spec.teardown is None  # 띄운 프로세스가 없으니 정리할 것도 없다


def test_unregistered_extension_is_not_monitored(project, monkeypatch):
    monitored = _patch(monkeypatch)
    out = job.watch(_deps(project), None)
    assert out["service"] == "확장 미등록" and not monitored


def test_registered_extension_alerts_via_telegram(project, monkeypatch):
    monitored = _patch(monkeypatch)
    _register(project)
    deps = _deps(project)
    out = job.watch(deps, None)
    assert out["service"] == "일반 Chrome 확장 수집 모드" and out["health"] == "healthy"
    # 텔레그램 알림은 messages 한 곳의 문구로
    monitored[-1]("수집 장애: process:runner")
    assert deps.notifier.msgs[-1].startswith("[세이브티커 수집]")


def test_disabled_by_config(project, monkeypatch):
    monitored = _patch(monkeypatch)
    _register(project)
    (project / "config" / "sources.yaml").write_text(
        "saveticker: {collect: false}\n", encoding="utf-8"
    )
    assert job.watch(_deps(project), None) == {"skipped": "꺼짐"}
    assert not monitored


def test_stop_event_skips_watch(project, monkeypatch):
    monitored = _patch(monkeypatch)
    _register(project)
    stop = threading.Event()
    stop.set()
    assert job.watch(_deps(project, stop=stop), None) == {"skipped": "종료 중"}
    assert not monitored


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


def test_restart_request_points_to_extension_popup(project, monkeypatch):
    """확장 수집은 Chrome 확장 아이콘에서 재개한다 — 안내만 보내고 플래그를 지운다."""
    _patch(monkeypatch)
    _register(project)
    deps = _deps(project)
    _request_restart(deps)
    out = job.watch(deps, None)
    assert "Chrome" in out["restart"] and _flag(deps) is None
    assert any("Chrome" in m for m in deps.notifier.msgs)
    assert "restart" not in job.watch(deps, None)  # 소비했으니 다음 틱엔 없음


def test_restart_notice_failure_is_logged_and_flag_still_deleted(project, monkeypatch, caplog):
    """안내 전송이 실패해도 로그로 남기고 플래그는 지운다 — 매 틱 반복하지 않는다."""
    _patch(monkeypatch)
    deps = _deps(project)

    def boom(text, *, escape=True):
        raise OSError("전송 실패")

    deps.notifier.send = boom
    _request_restart(deps)
    with caplog.at_level("ERROR", logger="trader.features.saveticker"):
        out = job.watch(deps, None)
    assert out["restart"].startswith("실패") and _flag(deps) is None
    assert any("다시 시작" in r.getMessage() and r.exc_info for r in caplog.records)
    assert "restart" not in job.watch(deps, None)


def test_first_tick_syncs_extension_and_notifies_once(project, monkeypatch):
    """앱 업데이트 뒤 첫 틱: 설치된 확장 복사본이 번들과 다르면 교체(`bridge_install.sync`)하고
    'Chrome 확장 새로고침' 안내를 한 번만 보낸다. 같은 프로세스의 다음 틱은 다시 보지 않는다."""
    from trader.features.saveticker import bridge_install

    _patch(monkeypatch)
    calls = []
    monkeypatch.setattr(
        bridge_install,
        "sync",
        lambda: calls.append(1) or {"extension_updated": True, "host_updated": False},
    )
    deps = _deps(project)
    out = job.watch(deps, None)
    assert out["extension_sync"]["extension_updated"] is True
    assert sum("새로고침" in m for m in deps.notifier.msgs) == 1
    assert "extension_sync" not in job.watch(deps, None)
    assert calls == [1]


def test_sync_without_update_is_silent_and_failure_is_isolated(project, monkeypatch):
    from trader.features.saveticker import bridge_install

    _patch(monkeypatch)
    monkeypatch.setattr(bridge_install, "sync", lambda: {"skipped": "not_installed"})
    deps = _deps(project)
    out = job.watch(deps, None)
    assert "extension_sync" not in out and not deps.notifier.msgs
    job.reset_state()

    def boom():
        raise OSError("synthetic")

    monkeypatch.setattr(bridge_install, "sync", boom)
    out = job.watch(deps, None)
    assert out["extension_sync"].startswith("실패") and out["service"] == "확장 미등록"
