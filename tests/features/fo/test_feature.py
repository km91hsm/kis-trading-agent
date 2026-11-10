from trader.features.fo.feature import FEATURE
from trader.kernel.registry import contracts, discover


def test_feature_declaration():
    assert FEATURE.id == "fo" and FEATURE.tier == "lane"
    assert [m.version for m in FEATURE.migrations] == [14]
    assert [(s.file, s.key) for s in FEATURE.sections] == [("settings", "fo"), ("schedule", "fo")]
    assert FEATURE.control_namespaces == (
        "fo_verified", "fo_halted", "fo_day_open", "fo_alerts", "fo_ref_iv", "fo_pending_entry",
        "fo_replace", "fo_enabled",
    )  # fmt: skip


def test_discovered_and_contracts_hold():
    assert "fo" in [f.id for f in discover()]
    assert contracts.check(discover()) == []
