import json

from typer.testing import CliRunner

from trader.kernel.cli.main import app


def test_import_and_status_and_query_need_no_browser_or_broker(project):
    p = project / "capture.json"
    p.write_text(
        json.dumps(
            {
                "stream": "news_all",
                "page_url": "https://www.saveticker.com/news",
                "observed_at": "2026-10-25T00:00:00Z",
                "items": [{"id": "x", "title": "속보", "tickers": ["AAPL"]}],
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    imported = runner.invoke(app, ["saveticker", "import-capture", str(p)])
    assert imported.exit_code == 0, imported.output
    status = runner.invoke(app, ["saveticker", "status"])
    assert status.exit_code == 0 and json.loads(status.output)["streams"]["news_all"]["count"] == 1
    q = runner.invoke(
        app, ["saveticker", "query", "--asof", "2026-10-25T00:01:00Z", "--ticker", "AAPL"]
    )
    assert q.exit_code == 0 and json.loads(q.output)[0]["item"]["title"] == "속보"
    assert not (project / "state" / "trader.db").exists()


def test_invalid_capture_does_not_echo_input_secrets(project):
    p = project / "capture.json"
    p.write_text('{"password":"NEVER_ECHO_THIS"}', encoding="utf-8")
    result = CliRunner().invoke(app, ["saveticker", "import-capture", str(p)])
    assert result.exit_code == 1
    assert "NEVER_ECHO_THIS" not in result.output


def test_status_without_data_does_not_create_database(project):
    result = CliRunner().invoke(app, ["saveticker", "status"])
    assert result.exit_code == 0
    assert json.loads(result.output)["streams"] == {}
    assert not (project / "state" / "external" / "saveticker" / "data" / "feed.sqlite3").exists()


def test_operational_layout_separates_data_from_runtime(project):
    from trader.features.saveticker.cli import root, runtime

    assert root() == project / "state" / "external" / "saveticker" / "data"
    assert runtime() == project / "state" / "external" / "saveticker" / "runtime"
