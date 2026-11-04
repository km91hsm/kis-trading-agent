import pytest

from trader.kernel.registry.discover import discover
from trader.kernel.registry.types import RegistryError


def test_discover_collects_sorted_features():
    feats = discover("tests.fixtures.features_ok")
    assert [f.id for f in feats] == ["alpha", "beta"]


def test_discover_without_feature_module_raises():
    with pytest.raises(RegistryError, match="delta"):
        discover("tests.fixtures.features_nofeature")


def test_discover_real_package_lists_accounts():
    ids = [f.id for f in discover()]
    assert ids == [
        "accounts", "briefing", "evaluation", "fo", "ingest", "ml", "quotes", "regime",
        "saveticker", "scalp", "scan", "spot",
    ]  # fmt: skip
