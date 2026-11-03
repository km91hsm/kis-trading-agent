"""briefing 기능 선언."""

from trader.features.briefing.feature import FEATURE
from trader.features.briefing.settings import BriefingCfg
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.platform.bundle import templates_dir
from trader.kernel.registry import discover


def test_registered_and_template_parity():
    assert FEATURE.id == "briefing" and FEATURE.tier == "service"
    assert [j.id for j in FEATURE.jobs] == ["morning_briefing"]
    assert [a.name for a in FEATURE.agents] == ["briefing"]
    reg = build_registry(discover())
    st = load("settings", reg, path=templates_dir() / "config" / "settings.yaml")
    assert st.briefing == BriefingCfg() and st.models["briefing"] == "haiku"
    sc = load("schedule", reg, path=templates_dir() / "config" / "schedule.yaml")
    assert sc.briefing == "07:20"
