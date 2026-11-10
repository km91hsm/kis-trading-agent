"""evaluation health 영역: 보정 재검토 시점(결정 14번)."""

from datetime import UTC, datetime

from tests.features.evaluation.helpers import seed_matured
from trader.features.evaluation import health
from trader.kernel.ops.types import OpsContext

NOW = datetime(2026, 10, 27, 3, 0, tzinfo=UTC)


def ctx(conn, project):
    return OpsContext(conn, None, project / "state", NOW, {}, [], [])


def test_area_key_is_feature_id_and_registered():
    from trader.features.evaluation.feature import FEATURE

    assert health.AREA.key == "evaluation" and FEATURE.health_areas == (health.AREA,)


def test_calibration_review_flags_at_threshold(conn, project):
    seed_matured(conn, 59)
    assert health.check(ctx(conn, project)) == []  # 기준(60) 미만
    seed_matured(conn, 1, start=datetime(2026, 9, 1).date())
    items = health.check(ctx(conn, project))
    assert [(i.key, i.level, i.count, i.link) for i in items] == [
        ("evaluation.calibration_review", "watch", 60, "/performance#calibration")
    ]
    assert "inject" in items[0].text
    (project / "config" / "ml.yaml").write_text(
        "models:\n  calibration:\n    inject: true\n", encoding="utf-8"
    )
    assert health.check(ctx(conn, project)) == []  # 주입이 켜져 있으면 알릴 것 없음


def test_calibration_review_errors_surface_as_area_error(conn, project, monkeypatch):
    """예외를 삼키지 않는다 — 러너가 `evaluation.error` warning 으로 격리한다(M-4)."""
    from trader.kernel.ops.health import _run_area

    def boom(_conn):
        raise RuntimeError("x")

    monkeypatch.setattr(health, "matured_count", boom)
    items = _run_area(health.AREA, ctx(conn, project))
    assert [(i.key, i.level) for i in items] == [("evaluation.error", "warning")]
    assert "RuntimeError: x" in items[0].text


def test_calibration_review_disabled_is_explicit_empty(conn, project, monkeypatch):
    """보정이 꺼져 있으면 원장을 보지 않고 빈 결과(정상 경로)."""
    (project / "config" / "ml.yaml").write_text(
        "models:\n  calibration:\n    enabled: false\n", encoding="utf-8"
    )

    def boom(_conn):
        raise AssertionError("꺼져 있으면 세지 않는다")

    monkeypatch.setattr(health, "matured_count", boom)
    assert health.check(ctx(conn, project)) == []
