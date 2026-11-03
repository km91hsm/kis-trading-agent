"""`trader scan theme show | events show` (CliRunner·scratch TRADER_HOME).

옛 `tests/test_daemon_theme.py::test_theme_show_cli_json` 이식(명령 `theme show`→`scan theme show`).
`events show` 는 신규."""

import json

from typer.testing import CliRunner

from trader.kernel.cli.main import app
from trader.kernel.ledger import control


def test_theme_show_cli_json(project, conn):
    control.set_flag(conn, "theme_heat:KR", {"market": "KR", "date": "2026-10-26", "themes": []})
    res = CliRunner().invoke(app, ["scan", "theme", "show", "--market", "KR"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["date"] == "2026-10-26"
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
    out = json.loads(res.output)
    assert out["enabled"] is True and out["market"] == "KR"
    assert [e["id"] for e in out["detected"]] == ["war_geopolitics"]
