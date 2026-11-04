from typer.testing import CliRunner

from trader.kernel.cli.main import app
from trader.kernel.registry import discover
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


NAV_REL = "web/src/generated/nav.ts"


def test_gen_nav_writes_then_check_passes(project):
    r = CliRunner().invoke(app, ["dev", "gen-nav"])
    assert r.exit_code == 0, r.output
    target = project / NAV_REL
    assert target.read_text(encoding="utf-8") == gen.nav_ts(discover())  # 기능 nav 포함
    assert b"\r\n" not in target.read_bytes()
    ok = CliRunner().invoke(app, ["dev", "gen-nav", "--check"])
    assert ok.exit_code == 0 and "nav 최신" in ok.output


def test_gen_nav_check_fails_when_stale(project):
    target = project / NAV_REL
    target.parent.mkdir(parents=True)
    target.write_text("// stale\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["dev", "gen-nav", "--check"])
    assert r.exit_code == 1 and "gen-nav" in r.output
