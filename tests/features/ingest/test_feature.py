"""ingest FEATURE 선언: 계약·섹션·CLI."""

from trader.features.ingest.feature import FEATURE
from trader.kernel.registry import contracts, discover


def test_contract_clean_and_both_features_discovered():
    feats = discover()
    assert {"accounts", "ingest", "saveticker"} <= {f.id for f in feats}
    assert contracts.check(feats) == []


def test_sections_cover_sources_universe_sectors():
    keys = {(s.file, s.key) for s in FEATURE.sections}
    for k in ("kis", "news", "macro", "dart", "edgar", "community", "channels"):
        assert ("sources", k) in keys
    assert ("schedule", "news_collect") in keys
    assert {f for f, _ in keys} >= {"universe", "sectors"}
    assert FEATURE.jobs[0].id == "news_collect"


def test_cli_mounted_as_ingest():
    assert FEATURE.cli is not None
    assert FEATURE.cli.info.name == "ingest"
