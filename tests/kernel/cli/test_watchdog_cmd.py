"""`trader watchdog`: 외부 감시 1회 판정을 JSON 한 줄로."""

import json

from typer.testing import CliRunner

from trader.kernel.cli.main import app


def test_cli_watchdog_quiet_prints_json(project):
    r = CliRunner().invoke(app, ["watchdog", "--quiet"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output.strip().splitlines()[-1])
    assert out["level"] in ("ok", "watch", "warning", "critical") and out["notified"] is False
    assert (project / "state" / "watchdog.json").exists()


def test_cli_watchdog_survives_broken_schedule(project):
    """schedule 오류로 데몬이 못 뜨는 상황에서도 감시는 잡 표 없이 판정을 계속한다."""
    (project / "config" / "schedule.yaml").write_text("bogus: 1\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["watchdog", "--quiet"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output.strip().splitlines()[-1])
    assert "level" in out
