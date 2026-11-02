import pytest

from trader.features.saveticker.settings import SaveTickerCfg, load_cfg
from trader.kernel.config import ConfigError


def test_defaults_when_sources_yaml_missing(project):
    cfg = load_cfg()
    assert cfg == SaveTickerCfg()
    assert cfg.dir == "state/external/saveticker"
    assert cfg.collect is True and cfg.retention_days == 30


def test_loads_saveticker_section(project):
    (project / "config" / "sources.yaml").write_text(
        'saveticker: {dir: "x/y", retention_days: 5}\n', encoding="utf-8"
    )
    cfg = load_cfg()
    assert cfg.dir == "x/y" and cfg.retention_days == 5 and cfg.collect is True


def test_rejects_unknown_key(project):
    (project / "config" / "sources.yaml").write_text("saveticker: {foo: 1}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_cfg()
