"""`trader install`: --dry-run 만 테스트한다(실제 등록은 사용자 전용)."""

import json
import sys

import pytest
from typer.testing import CliRunner

from trader.kernel.cli.main import app


@pytest.mark.skipif(
    sys.platform == "linux", reason="install 은 macOS(launchd)·Windows(작업 스케줄러) 전용"
)
def test_cli_install_dry_run(project):
    r = CliRunner().invoke(app, ["install", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "launchctl" in r.stdout or "schtasks" in r.stdout
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["kind"] == "daemon" and out["written"] is False
    assert set(out) == {"path", "command", "written", "content", "kind"}
    w = CliRunner().invoke(app, ["install", "--dry-run", "--watchdog"])
    assert w.exit_code == 0, w.output
    wout = json.loads(w.stdout.strip().splitlines()[-1])
    assert wout["kind"] == "watchdog" and "watchdog" in wout["path"]
