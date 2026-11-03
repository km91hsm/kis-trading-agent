"""ml 기능 선언."""

from trader.features.ml.feature import FEATURE
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.platform.bundle import templates_dir
from trader.kernel.registry import discover


def test_feature_declaration():
    assert FEATURE.id == "ml" and FEATURE.tier == "service"
    assert [m.version for m in FEATURE.migrations] == [12]
    assert {(s.file, s.key) for s in FEATURE.sections} == {
        ("ml", "label"),
        ("ml", "train"),
        ("ml", "models"),
        ("schedule", "ml_train"),
        ("schedule", "universe_members"),
        ("schedule", "features_snapshot"),
    }


def test_registered_and_template_parity():
    reg = build_registry(discover())
    assert set(reg.keys("ml")) == {"label", "train", "models"}
    load("ml", reg, path=templates_dir() / "config" / "ml.yaml")


def test_universe_members_table(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(universe_members)")]
    assert cols == ["market", "ticker", "first_seen", "last_seen", "source"]
