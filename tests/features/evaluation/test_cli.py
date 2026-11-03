import json

from typer.testing import CliRunner

from tests.features.evaluation.helpers import seed_matured
from trader.kernel.cli.main import app


def test_cli_calibration_build(project, conn):
    seed_matured(conn, 60)
    conn.commit()
    r = CliRunner().invoke(app, ["evaluation", "calibration", "build"])
    assert r.exit_code == 0, r.stdout
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["active"] is True and out["n_decisions"] == 60


def test_cli_calibration_build_inactive(project, conn):
    r = CliRunner().invoke(app, ["evaluation", "calibration", "build"])
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout.strip().splitlines()[-1]) == {"active": False, "min_decisions": 60}


def test_cli_paper_review_json(project, conn):
    r = CliRunner().invoke(app, ["evaluation", "paper-review", "--days", "7"])
    assert r.exit_code == 0, r.stdout
    out = json.loads(r.stdout)
    assert {"since", "until", "items", "alerts"} <= set(out)
    assert CliRunner().invoke(app, ["evaluation", "paper-review", "--days", "0"]).exit_code != 0
