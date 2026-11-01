from typer.testing import CliRunner

from trader.kernel.cli.main import app


def test_init_copies_only_missing_templates(project):
    (project / "config").rmdir()
    for name in ("state", "docs", "secrets"):
        (project / name).rmdir()
    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    for name in ("config", "secrets", "state", "docs"):
        assert (project / name).is_dir()
    settings = project / "config" / "settings.yaml"
    assert settings.exists() and "settings.yaml" in r.stdout
    assert (project / "config" / "schedule.yaml").exists()
    settings.write_text("markets: [KR]\n", encoding="utf-8")
    r2 = CliRunner().invoke(app, ["init"])
    assert r2.exit_code == 0
    assert settings.read_text(encoding="utf-8") == "markets: [KR]\n"
    assert "settings.yaml" not in r2.stdout and "schedule.yaml" not in r2.stdout
