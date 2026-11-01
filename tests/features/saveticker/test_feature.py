from typer.testing import CliRunner

from trader.features.saveticker.feature import FEATURE
from trader.features.saveticker.settings import SECTION
from trader.kernel.cli.main import app
from trader.kernel.registry import check, discover


def test_feature_discovered_and_contract_clean():
    feats = discover()
    assert "saveticker" in [f.id for f in feats] and check(feats) == []
    assert FEATURE.tier == "service" and [j.id for j in FEATURE.jobs] == ["saveticker"]


def test_section_registered_in_sources_file():
    assert FEATURE.sections == (SECTION,)
    assert (SECTION.file, SECTION.key) == ("sources", "saveticker")


def test_cli_mounted_as_saveticker(project):
    result = CliRunner().invoke(app, ["saveticker", "--help"])
    assert result.exit_code == 0, result.output
    assert "status" in result.output
