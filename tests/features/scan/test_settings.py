"""`discovery` 설정(옛 `DiscoveryCfg`)·템플릿 패리티."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from trader.features.scan.settings import DiscoveryCfg
from trader.kernel.platform.bundle import templates_dir


def test_discovery_defaults():
    d = DiscoveryCfg().intraday
    assert d.enabled is True and d.min_items == 1 and d.discover_cap == 20
    assert (d.similarity.jaccard, d.similarity.ratio, d.similarity.lookback_days) == (0.6, 0.8, 3)


def test_discovery_rejects_unknown_and_out_of_range():
    with pytest.raises(ValidationError):
        DiscoveryCfg(intraday={"nope": 1})
    with pytest.raises(ValidationError):
        DiscoveryCfg(intraday={"similarity": {"jaccard": 1.5}})


def test_discovery_template_matches_model(monkeypatch):
    monkeypatch.setenv("TRADER_HOME", str(Path(__file__).resolve().parents[3]))
    raw = yaml.safe_load(
        (templates_dir() / "config" / "settings.yaml").read_text(encoding="utf-8")
    )
    assert DiscoveryCfg(**raw["discovery"]) == DiscoveryCfg()


def test_settings_loads_discovery(settings):
    assert settings.discovery.intraday.discover_cap == 20
