import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from trader.kernel.cli.main import app
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations


def test_selfcheck_json_has_version_and_features(project):
    r = CliRunner().invoke(app, ["selfcheck"])
    assert r.exit_code == 0, r.output
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert list(data) == ["python", "root", "ledger_version", "features", "web_dist"]
    assert data["python"] == ".".join(str(x) for x in sys.version_info[:3])
    assert Path(data["root"]) == project.resolve()
    assert data["ledger_version"] == max(m.version for m in all_migrations(discover()))
    assert data["features"] == ["accounts"]
    assert data["web_dist"] is False
