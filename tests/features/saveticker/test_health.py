"""saveticker health 영역: 확장이 등록됐을 때만 연결 끊김·사이트 확인 멈춤 warning(2026-10-29)."""

import os
from datetime import UTC, datetime

from trader.features.saveticker import automation, health, paths
from trader.features.saveticker.store import atomic_json
from trader.kernel.daemon.lock import Lock
from trader.kernel.ops.types import OpsContext

NOW = datetime(2026, 10, 23, 8, 30, tzinfo=UTC)


def ctx(project):
    return OpsContext(None, None, project / "state", NOW, {}, [], [])


def items(project):
    return {i.key: i for i in health.check(ctx(project))}


def test_area_key_is_feature_id_and_registered():
    from trader.features.saveticker.feature import FEATURE

    assert health.AREA.key == "saveticker" and FEATURE.health_areas == (health.AREA,)


def test_unregistered_extension_is_not_checked(project):
    assert items(project) == {}  # 확장 미등록이면 점검 대상이 아니다


def test_disconnected_extension_is_warning(project):
    atomic_json(paths.home() / "runtime/bridge-config.json", {"backend": "extension"})
    got = items(project)
    assert got["saveticker.disconnected"].level == "warning"
    assert got["saveticker.disconnected"].link == "/system/features#saveticker"
    assert "saveticker.stopped" not in got


def test_verification_stop_is_warning_with_reason(project):
    runtime = paths.home() / "runtime"
    atomic_json(runtime / "bridge-config.json", {"backend": "extension"})
    atomic_json(runtime / "bridge.json", {"pid": os.getpid(), "state": "waiting_verification",
                                          "reason": "verification_required",
                                          "updated_at": datetime.now(UTC).isoformat()})  # fmt: skip
    lock = Lock(runtime / "bridge-host.lock")
    assert lock.acquire()
    try:
        got = items(project)
    finally:
        lock.release()
    assert "saveticker.disconnected" not in got
    stopped = got["saveticker.stopped"]
    assert stopped.level == "warning" and "재확인" in stopped.text


def test_saveticker_check_failure_surfaces_as_area_error(project, monkeypatch):
    """예외를 삼키지 않는다 — 러너가 `saveticker.error` warning 으로 격리한다(M-4)."""
    from trader.kernel.ops.health import _run_area

    atomic_json(paths.home() / "runtime/bridge-config.json", {"backend": "extension"})

    def boom(home):
        raise OSError("읽기 실패")

    monkeypatch.setattr(automation, "status", boom)
    got = _run_area(health.AREA, ctx(project))
    assert [(i.key, i.level) for i in got] == [("saveticker.error", "warning")]
    assert "읽기 실패" in got[0].text


def _blocked_with_diagnostic(project, recorded_at):
    runtime = paths.home() / "runtime"
    at = datetime.now(UTC).isoformat()
    atomic_json(runtime / "bridge-config.json", {"backend": "extension"})
    atomic_json(runtime / "bridge.json", {"pid": os.getpid(), "state": "waiting_verification",
                                          "reason": "verification_required",
                                          "updated_at": at, "at": at})  # fmt: skip
    atomic_json(runtime / "extension-diagnostics.json",
                {"category": "recheck", "reason": "page_unverified", "reload_count": 1,
                 "terminal": False, "recorded_at": recorded_at or at})  # fmt: skip
    lock = Lock(runtime / "bridge-host.lock")
    assert lock.acquire()
    try:
        return items(project)
    finally:
        lock.release()


def test_fresh_retrying_recovery_is_watch_not_stopped_warning(project):
    got = _blocked_with_diagnostic(project, None)
    assert "saveticker.stopped" not in got
    item = got["saveticker.retrying"]
    assert item.level == "watch" and item.link == "/system/features#saveticker"
    assert item.text == "세이브티커 복구 재시도 중 — 자동으로 다시 시도합니다"


def test_stale_retrying_diagnostic_keeps_stopped_warning(project):
    got = _blocked_with_diagnostic(project, "2026-10-01T00:00:00+00:00")
    assert got["saveticker.stopped"].level == "warning" and "saveticker.retrying" not in got
