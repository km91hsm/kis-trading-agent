import pytest

from tests.features.accounts.conftest import make_account  # noqa: F401
from trader.features.spot.settings import SECTIONS, DebateCfg, RiskLimits
from trader.kernel.cli.boot import build_registry
from trader.kernel.config import ConfigError
from trader.kernel.config.loader import load
from trader.kernel.platform.bundle import templates_dir
from trader.kernel.registry import discover


def test_defaults(settings):
    r = settings.risk
    assert (r.max_position_pct, r.max_positions, r.daily_loss_stop_pct) == (0.25, 5, -0.05)
    assert r.horizon_profiles["short"].periodic_check_min == 60
    assert r.reprice.after_min == 30
    assert settings.debate_rounds == 1
    assert settings.debate == DebateCfg(skip_on_consensus=True, consensus_min_conviction=0.7)


def test_horizon_profiles_require_all_three():
    prof = RiskLimits().horizon_profiles["mid"].model_dump()
    with pytest.raises(ValueError):
        RiskLimits(horizon_profiles={"short": prof, "mid": prof})
    RiskLimits(horizon_profiles={"short": prof, "mid": prof, "long": prof})


def test_template_loads(monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("TRADER_HOME", str(Path(__file__).resolve().parents[3]))
    cfg = load(
        "settings", build_registry(discover()), path=templates_dir() / "config" / "settings.yaml"
    )
    assert cfg.risk == RiskLimits()
    assert cfg.debate_rounds == 1 and cfg.debate == DebateCfg()


def test_section_keys_and_debate_rounds_bounds(project):
    assert [(s.file, s.key) for s in SECTIONS] == [
        ("settings", "risk"),
        ("settings", "debate_rounds"),
        ("settings", "debate"),
    ]
    p = project / "config" / "settings.yaml"
    p.write_text("debate_rounds: 3\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load("settings", build_registry(discover()), path=p)
