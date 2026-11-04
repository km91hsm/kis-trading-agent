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
        ("evaluation.calibration_review", "watch", 60, "/performance")
    ]
    assert "inject" in items[0].text
    (project / "config" / "ml.yaml").write_text(
        "models:\n  calibration:\n    inject: true\n", encoding="utf-8"
    )
    assert health.check(ctx(conn, project)) == []  # 주입이 켜져 있으면 알릴 것 없음


def test_calibration_review_swallows_errors(conn, project, monkeypatch):
    def boom(_conn):
        raise RuntimeError("x")

    monkeypatch.setattr(health, "matured_count", boom)
    assert health.check(ctx(conn, project)) == []
