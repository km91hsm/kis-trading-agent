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
    assert stopped.link == "/system#saveticker"
    atomic_json(runtime / "service.json", {"state": "running"})
    lane = {"community": {"reason": "AccessDenied", "at": NOW.isoformat()}}
    atomic_json(runtime / "lane-blocked.json", lane)
    got = items()
    assert "saveticker.stopped" not in got
    paused = got["saveticker.lane_paused"]
    assert paused.level == "watch" and "community" in paused.text
    assert paused.link == "/system#saveticker"


def test_saveticker_check_failure_returns_empty(project, monkeypatch, caplog):
    paths.credentials_path().write_text("x", encoding="utf-8")

    def boom(home):
        raise OSError("읽기 실패")

    monkeypatch.setattr(automation, "status", boom)
    with caplog.at_level("DEBUG", logger="trader.features.saveticker"):
        assert health.check(ctx(project)) == []
    assert "읽기 실패" in caplog.text
