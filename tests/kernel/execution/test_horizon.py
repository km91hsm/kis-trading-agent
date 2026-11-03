"""보유기간 프로필(Phase 23 T): 이름 판정·재분석 주기·설정 검증."""

from datetime import date

import pytest

from trader.features.spot.settings import RiskLimits
from trader.kernel.execution import horizon


@pytest.fixture
def settings(project):
    from trader.kernel.cli.boot import build_registry
    from trader.kernel.config.loader import load
    from trader.kernel.registry import discover

    return load("settings", build_registry(discover()), path=project / "config" / "settings.yaml")


def test_name_of_bands_and_fallback():
    assert horizon.name_of(1) == horizon.name_of(10) == "short"
    assert horizon.name_of(11) == horizon.name_of(40) == "mid"
    assert horizon.name_of(41) == horizon.name_of(250) == "long"
    assert horizon.name_of(None) == horizon.name_of(0) == horizon.name_of("x") == "mid"


def test_profile_defaults_and_of_decision(settings):
    r = settings.risk
    name, p = horizon.of_decision(r, {"horizon_days": 5})
    assert name == "short" and p.move_pct == 0.04 and p.periodic_check_min == 60
    name, p = horizon.of_decision(r, None)
    assert name == "mid" and p.reeval_interval_min == 30 and p.periodic_check_min == 0
    assert horizon.profile_of(r, 60).news_min_novel == 3


def test_reanalyze_due_by_trading_days(settings):
    r = settings.risk
    long_ = r.horizon_profiles["long"]  # 5 운영일마다
    fri = date(2026, 10, 23)
    assert horizon.reanalyze_due(long_, None, fri)
    assert not horizon.reanalyze_due(long_, fri, date(2026, 10, 29))  # 4 평일 뒤
    assert horizon.reanalyze_due(long_, fri, date(2026, 10, 30))  # 5 평일 뒤
    assert horizon.reanalyze_due(r.horizon_profiles["short"], fri, date(2026, 10, 26))
    assert not horizon.reanalyze_due(long_, fri, fri)  # 오늘 결정이 이미 있음


def test_profiles_must_be_complete(settings):
    with pytest.raises(ValueError):
        RiskLimits.model_validate(
            {"horizon_profiles": {"short": settings.risk.horizon_profiles["short"].model_dump()}}
        )
