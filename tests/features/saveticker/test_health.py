"""saveticker health 영역: 수집 멈춤 warning, 접근 거부로 쉬는 작업 watch(2026-10-29)."""

from datetime import UTC, datetime

from trader.features.saveticker import automation, health, paths
from trader.features.saveticker.store import atomic_json
from trader.kernel.ops.types import OpsContext

NOW = datetime(2026, 10, 23, 8, 30, tzinfo=UTC)


def ctx(project):
    return OpsContext(None, None, project / "state", NOW, {}, [], [])


def test_area_key_is_feature_id_and_registered():
    from trader.features.saveticker.feature import FEATURE

    assert health.AREA.key == "saveticker" and FEATURE.health_areas == (health.AREA,)


def test_saveticker_stopped_is_warning_and_paused_lane_is_watch(project):
    def items():
        return {i.key: i for i in health.check(ctx(project))}

    assert items() == {}  # 계정 파일이 없으면 점검 대상이 아니다
    paths.credentials_path().write_text("x", encoding="utf-8")
    runtime = paths.home() / "runtime"
    blocked = {"state": "blocked", "reason": "authentication", "at": NOW.isoformat()}
    atomic_json(runtime / "service.json", blocked)
    got = items()
    stopped = got["saveticker.stopped"]
    assert stopped.level == "warning" and "인증 실패" in stopped.text
    assert stopped.link == "/system/features#saveticker"
    atomic_json(runtime / "service.json", {"state": "running"})
    lane = {"community": {"reason": "AccessDenied", "at": NOW.isoformat()}}
    atomic_json(runtime / "lane-blocked.json", lane)
    got = items()
    assert "saveticker.stopped" not in got
    paused = got["saveticker.lane_paused"]
    assert paused.level == "watch" and "community" in paused.text
    assert paused.link == "/system/features#saveticker"


def test_saveticker_check_failure_surfaces_as_area_error(project, monkeypatch):
    """예외를 삼키지 않는다 — 러너가 `saveticker.error` warning 으로 격리한다(M-4)."""
    from trader.kernel.ops.health import _run_area

    paths.credentials_path().write_text("x", encoding="utf-8")

    def boom(home):
        raise OSError("읽기 실패")

    monkeypatch.setattr(automation, "status", boom)
    items = _run_area(health.AREA, ctx(project))
    assert [(i.key, i.level) for i in items] == [("saveticker.error", "warning")]
    assert "읽기 실패" in items[0].text


def test_disconnected_extension_is_checked_without_credentials(project):
    atomic_json(paths.home() / "runtime/bridge-config.json", {"backend": "extension"})
    items = health.check(ctx(project))
    assert any(i.key == "saveticker.disconnected" and i.level == "warning" for i in items)
