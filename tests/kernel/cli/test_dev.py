from typer.testing import CliRunner

from trader.kernel.cli.main import app
from trader.kernel.web import gen

REL = "web/src/generated/sse-keys.ts"


def test_gen_writes_then_check_passes(project):
    r = CliRunner().invoke(app, ["dev", "gen-sse-keys"])
    assert r.exit_code == 0, r.output
    target = project / REL
    assert target.read_text(encoding="utf-8") == gen.sse_keys_ts([])
    assert b"\r\n" not in target.read_bytes()
    assert CliRunner().invoke(app, ["dev", "gen-sse-keys", "--check"]).exit_code == 0


def test_dev_gen_sse_keys_check_fails_when_stale(project):
    target = project / REL
    target.parent.mkdir(parents=True)
    target.write_text("// stale\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["dev", "gen-sse-keys", "--check"])
    assert r.exit_code == 1
    assert "sse-keys" in r.output
    assert target.read_text(encoding="utf-8") == "// stale\n"


def test_check_fails_when_missing(project):
    assert CliRunner().invoke(app, ["dev", "gen-sse-keys", "--check"]).exit_code == 1
