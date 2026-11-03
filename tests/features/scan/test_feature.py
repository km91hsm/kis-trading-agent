"""scan 기능 선언·설정 템플릿."""

from pathlib import Path

import yaml

from trader.kernel.platform.bundle import templates_dir


def test_feature_declares_theme_sections_and_v11():
    from trader.features.scan.feature import FEATURE
    from trader.features.scan.settings import ThemeCfg, ThemeSchedule

    assert FEATURE.id == "scan" and FEATURE.tier == "service"
    assert {(s.file, s.key, s.model) for s in FEATURE.sections if s.file != "events"} == {
        ("settings", "theme", ThemeCfg),
        ("schedule", "theme", ThemeSchedule),
    }
    assert {s.key for s in FEATURE.sections if s.file == "events"} == {
        "enabled", "lookback_hours", "max_picks_per_event", "max_event_picks", "events",
    }  # fmt: skip
    assert [h.point for h in FEATURE.hooks] == ["news.post_ingest"]
    assert [(m.version, m.name, m.owner) for m in FEATURE.migrations] == [
        (11, "scan_theme_heat", "scan")
    ]


def test_theme_template_matches_model_fields(monkeypatch):
    from trader.features.scan.settings import ThemeCfg, ThemeSchedule

    monkeypatch.setenv("TRADER_HOME", str(Path(__file__).resolve().parents[3]))
    cfg = templates_dir() / "config"
    raw = yaml.safe_load((cfg / "settings.yaml").read_text(encoding="utf-8"))
    assert set(raw["theme"]) == set(ThemeCfg.model_fields)
    assert "interval_s" not in raw["theme"]
    assert ThemeCfg(**raw["theme"]) == ThemeCfg()
    sched = yaml.safe_load((cfg / "schedule.yaml").read_text(encoding="utf-8"))
    assert ThemeSchedule(**sched["theme"]) == ThemeSchedule(interval_s=1800)
