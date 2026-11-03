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


def test_cli_reflect_fake(project, conn, monkeypatch):
    from datetime import UTC, datetime

    from trader.features.evaluation.memory import store

    monkeypatch.setenv("FAKE_CLAUDE", "1")
    (eid,) = seed_matured(conn, 1, now=datetime.now(UTC))
    conn.commit()
    r = CliRunner().invoke(app, ["evaluation", "reflect", "--market", "KR", "--dry-run"])
    assert r.exit_code == 0, r.stdout
    assert "결과 채움 0건" in r.stdout and "대기 중 반성 1건 (--dry-run: 세션 생략)" in r.stdout
    assert store.get_entry(conn, eid)["reflection"] is None
    r = CliRunner().invoke(app, ["evaluation", "reflect", "--market", "KR"])
    assert r.exit_code == 0, r.stdout
    assert "반성 1건 (FAKE), 정리 0건 삭제" in r.stdout
    assert store.get_entry(conn, eid)["reflection"] is not None
    assert CliRunner().invoke(app, ["evaluation", "reflect", "--market", "JP"]).exit_code == 1


def test_cli_rl_train_models_promote_demote(project, conn, monkeypatch):
    from tests.features.ml.helpers import synthetic_features
    from trader.features.evaluation.rl.policy import FixedPolicy
    from trader.features.ml import api as ml_api

    ml_api.write_rows(synthetic_features(3, 400, signal=True))
    monkeypatch.setattr(
        "trader.features.evaluation.rl.learners.fit",
        lambda s, c, *, seed, backend=None: (FixedPolicy(0.1), "fake"),
    )
    runner = CliRunner()
    out = runner.invoke(app, ["evaluation", "rl", "train", "--seeds", "1"])
    assert out.exit_code == 0, out.stdout
    v = json.loads(out.stdout.strip().splitlines()[-1])["version"]
    assert "미승격" in runner.invoke(app, ["evaluation", "rl", "models"]).stdout
    assert runner.invoke(app, ["evaluation", "rl", "promote", v]).exit_code == 0
    assert ml_api.registry_latest("rl_sizer") is not None
    assert runner.invoke(app, ["evaluation", "rl", "demote", v]).exit_code == 0
    assert ml_api.registry_latest("rl_sizer") is None
    assert runner.invoke(app, ["evaluation", "rl", "promote", "19990101_000000"]).exit_code == 1


def test_cli_rl_train_seeds_range(project):
    runner = CliRunner()
    assert runner.invoke(app, ["evaluation", "rl", "train", "--seeds", "0"]).exit_code == 2
    assert runner.invoke(app, ["evaluation", "rl", "train", "--seeds", "11"]).exit_code == 2
