"""regime 기능 선언·설정 템플릿."""

from pathlib import Path

import yaml

from trader.kernel.platform.bundle import templates_dir


def test_feature_declares_sections_and_migration_v10():
    from trader.features.regime.feature import FEATURE
    from trader.features.regime.settings import BlackoutCfg, HedgeCfg

    assert FEATURE.id == "regime" and FEATURE.tier == "service"
    assert {(s.file, s.key, s.model) for s in FEATURE.sections} == {
        ("settings", "hedge", HedgeCfg),
        ("settings", "event_blackout", BlackoutCfg),
    }
    assert [(m.version, m.name, m.owner) for m in FEATURE.migrations] == [
        (10, "regime_market_views", "regime")
    ]


def test_settings_template_has_every_hedge_field(monkeypatch):
    from trader.features.regime.settings import BlackoutCfg, HedgeCfg

    monkeypatch.setenv("TRADER_HOME", str(Path(__file__).resolve().parents[3]))
    path = templates_dir() / "config" / "settings.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(raw["hedge"]) == set(HedgeCfg.model_fields)
    assert set(raw["event_blackout"]) == set(BlackoutCfg.model_fields)
    # 템플릿 값 = 모델 기본값(옛 값 그대로)
    assert HedgeCfg(**raw["hedge"]) == HedgeCfg()
    assert BlackoutCfg(**raw["event_blackout"]) == BlackoutCfg()
