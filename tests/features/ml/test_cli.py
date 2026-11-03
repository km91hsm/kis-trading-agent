import json

import pytest
from typer.testing import CliRunner

from tests.features.ml.helpers import synthetic_features
from trader.features.ml import features, registry
from trader.kernel.cli.main import app


def _fast_ml_yaml(project):
    (project / "config" / "ml.yaml").write_text(
        "models:\n  quant: {max_iter: 40}\n", encoding="utf-8"
    )


@pytest.mark.slow
def test_cli_ml_train_models_promote(project):
    _fast_ml_yaml(project)
    features.write_rows(synthetic_features(6, 320, signal=True))
    runner = CliRunner()
    r = runner.invoke(app, ["ml", "train", "--market", "KR"])
    assert r.exit_code == 0, r.stdout
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["status"] == "trained" and out["version"] and out["promoted"] is True
    r = runner.invoke(app, ["ml", "models"])
    assert r.exit_code == 0 and out["version"] in r.stdout and "승격" in r.stdout
    r = runner.invoke(app, ["ml", "promote", out["version"], "--reason", "수동"])
    assert r.exit_code == 0 and registry.latest("quant")[1]["promote_reason"] == "수동"
    assert runner.invoke(app, ["ml", "promote", "nope"]).exit_code == 1


def test_cli_ml_train_insufficient(project):
    features.write_rows(synthetic_features(1, 30, signal=False))
    r = CliRunner().invoke(app, ["ml", "train"])
    assert r.exit_code == 0
    assert json.loads(r.stdout.strip().splitlines()[-1])["status"] == "insufficient_data"


@pytest.mark.slow
def test_cli_ml_demote(project):
    _fast_ml_yaml(project)
    features.write_rows(synthetic_features(6, 320, signal=True))
    runner = CliRunner()
    out = json.loads(runner.invoke(app, ["ml", "train"]).stdout.strip().splitlines()[-1])
    assert out["promoted"] is True
    r = runner.invoke(app, ["ml", "demote", out["version"], "--reason", "검토"])
    assert r.exit_code == 0 and registry.latest("quant") is None


def test_cli_ml_train_horizon_override(project, monkeypatch):
    """`--horizon N` 은 설정 파일을 바꾸지 않고 그 실행의 label.horizon_days 만 덮어쓴다."""
    from trader.features.ml import train as train_mod

    seen = {}

    def fake_train(cfg, market=None, min_rows=100):
        seen["h"] = cfg.label.horizon_days
        return {"status": "trained", "promoted": False}

    monkeypatch.setattr(train_mod, "train_quant", fake_train)
    r = CliRunner().invoke(app, ["ml", "train", "--market", "KR", "--horizon", "10"])
    assert r.exit_code == 0, r.stdout
    assert seen["h"] == 10
    r = CliRunner().invoke(app, ["ml", "train", "--market", "KR"])
    assert seen["h"] == 5  # 기본은 ml.yaml 값


def test_cli_ml_train_horizon_rejects_invalid(project):
    for h in ("0", "-1", "61"):
        r = CliRunner().invoke(app, ["ml", "train", "--market", "KR", "--horizon", h])
        assert r.exit_code != 0 and "Traceback" not in (r.stdout + (r.stderr or "")), h


@pytest.mark.slow
def test_cli_ml_tune_outputs_json(project, monkeypatch):
    from trader.features.ml import tune

    _fast_ml_yaml(project)
    features.write_rows(synthetic_features(6, 320, signal=True))
    monkeypatch.setitem(
        tune.GRIDS, "small", {"learning_rate": [0.05], "num_leaves": [7], "max_iter": [20]}
    )
    r = CliRunner().invoke(app, ["ml", "tune", "--market", "KR"])
    assert r.exit_code == 0, r.stdout
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["status"] == "tuned" and out["best"]["params"]["num_leaves"] == 7
    assert CliRunner().invoke(app, ["ml", "tune", "--grid", "huge"]).exit_code != 0


def test_cli_features_backfill_uses_accounts_market_client(project, monkeypatch):
    from tests.features.ingest.helpers import FakeMarket
    from trader.features.ml import cli as ml_cli

    (project / "config" / "universe.yaml").write_text("kr: ['005930']\n", encoding="utf-8")
    monkeypatch.setattr(ml_cli, "_market_client", lambda b: FakeMarket(candles=40))
    r = CliRunner().invoke(
        app, ["ml", "features", "backfill", "--years", "1", "--market", "KR"]
    )
    assert r.exit_code == 0, r.output
    assert "backfill: " in r.output and "행 저장" in r.output
    assert list((project / "state" / "features").glob("*.parquet"))


def test_cli_features_derive_without_market_client(project, monkeypatch):
    from trader.features.ml import cli as ml_cli

    def boom(b):
        raise AssertionError("derive 는 시세 클라이언트를 만들지 않는다")

    monkeypatch.setattr(ml_cli, "_market_client", boom)
    features.write_rows(synthetic_features(2, 10, signal=False))
    r = CliRunner().invoke(app, ["ml", "features", "derive"])
    assert r.exit_code == 0, r.output
    assert "derive: " in r.output and "개 파일 갱신" in r.output
