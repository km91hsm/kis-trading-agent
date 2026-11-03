"""`trader scan theme show | events show` (CliRunner·scratch TRADER_HOME).

옛 `tests/test_daemon_theme.py::test_theme_show_cli_json` 이식(명령 `theme show`→`scan theme show`).
`events show` 는 신규."""

import json
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import yaml
from typer.testing import CliRunner

from trader.features.ml import features
from trader.kernel.cli.main import app
from trader.kernel.ledger import control


def test_theme_show_cli_json(project, conn):
    control.set_flag(conn, "theme_heat:KR", {"market": "KR", "date": "2026-10-26", "themes": []})
    res = CliRunner().invoke(app, ["scan", "theme", "show", "--market", "KR"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["date"] == "2026-10-26"
    assert len(res.output.strip().splitlines()) == 1  # JSON 1줄 규약
    res = CliRunner().invoke(app, ["scan", "theme", "show", "--market", "US"])
    out = json.loads(res.output)
    assert out["themes"] == [] and "아직 결과 없음" in out["note"]
    res = CliRunner().invoke(app, ["scan", "theme", "show", "--market", "JP"])
    assert res.exit_code == 1


def test_events_show_prints_detected(project, conn):
    from trader.features.ingest import api as ingest_api

    (project / "config" / "events.yaml").write_text(
        "enabled: true\nevents:\n"
        "  - {id: war_geopolitics, label: 전쟁, kind: shock, min_news: 1, keywords: [공습],\n"
        "     beneficiaries: {KR: [{ticker: '012450', name: 한화에어로스페이스, role: 방산}]}}\n",
        encoding="utf-8",
    )
    ingest_api.insert_items(
        conn,
        [{"url": "https://ex.test/KR/0", "title": "공습 개시"}],
        market="KR",
        source="test",
        query="q",
        lang="ko",
        names={},
    )
    res = CliRunner().invoke(app, ["scan", "events", "show", "--market", "KR"])
    assert res.exit_code == 0, res.output
    assert len(res.output.strip().splitlines()) == 1  # JSON 1줄 규약
    out = json.loads(res.output)
    assert out["enabled"] is True and out["market"] == "KR"
    assert [e["id"] for e in out["detected"]] == ["war_geopolitics"]


def _scan_setup(project, monkeypatch):
    (project / "config" / "universe.yaml").write_text(
        yaml.safe_dump({"kr": ["000001", "000002", "000003"], "watch": [], "exclude": []}),
        encoding="utf-8",
    )
    rows = [
        {
            "date": date(2026, 10, 8), "ticker": t, "market": "KR", "close": 100.0, "volume": v,
            "atr_14": 1.0, "roc_10": r, "high_52w_pos": 0.5, "vol_z_20": 0.0,
        }
        for t, v, r in (
            ("000001", 1_000_000, 5.0),
            ("000002", 2_000_000, 1.0),
            ("000003", 3_000_000, 3.0),
        )
    ]  # fmt: skip
    features.write_rows(_full(pd.DataFrame(rows)))
    monkeypatch.setenv("FAKE_CLAUDE", "1")


def _full(df):
    """피처 스토어가 요구하는 전체 열을 채운다 (없는 열은 NaN)."""
    for c in features.FEATURE_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    return df


def test_cli_scan_fake(project, monkeypatch):
    _scan_setup(project, monkeypatch)
    r = CliRunner().invoke(
        app, ["scan", "run", "--market", "KR", "--asof", "2026-10-09T00:00:00+00:00"]
    )
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["market"] == "KR" and len(out["candidates"]) == 3
    # 거래대금 하위 30%(000001) 제외, 모멘텀 순
    assert [p["ticker"] for p in out["picks"]] == ["000003", "000002"]
    f = project / "state" / "runs" / "2026-10-09" / "KR" / "_scan.json"
    assert json.loads(f.read_text(encoding="utf-8"))["analyze_targets"][0]["ticker"] == "000003"


def test_cli_scan_rejects_bad_market(project, monkeypatch):
    _scan_setup(project, monkeypatch)
    assert CliRunner().invoke(app, ["scan", "run", "--market", "XX"]).exit_code == 1


def test_cli_scan_uses_ml_rank_when_promoted(project, monkeypatch):
    from tests.features.ml.helpers import synthetic_features
    from trader.features.ml import api as ml_api
    from trader.features.ml import dataset, registry

    _scan_setup(project, monkeypatch)
    df = dataset.make_labels(
        synthetic_features(3, 120, signal=True), horizon_days=5, up=0.02, down=-0.02
    )
    cols = dataset.feature_columns(df)
    X, y = dataset.split_xy(df, cols)
    v = registry.save(
        "quant",
        ml_api.QuantModel(cols).fit(X, y),
        {"features": cols},
        now=lambda: datetime(2026, 10, 17, tzinfo=UTC),
    )
    registry.promote("quant", v)
    r = CliRunner().invoke(
        app, ["scan", "run", "--market", "KR", "--asof", "2026-10-09T00:00:00+00:00", "--dry-run"]
    )
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["ml_model"] == f"quant@{v}"
    assert any("ML P(up)" in rs for c in out["candidates"] for rs in c["reasons"])
