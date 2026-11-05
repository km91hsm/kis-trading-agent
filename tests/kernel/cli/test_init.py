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


def test_init_copies_env_example_never_env(project):
    """`.env.example` 만 secrets/ 에 떨어뜨리고 `.env` 는 만들지 않는다.
    두 번째 호출은 복사 없음."""
    from trader.kernel.platform import bundle

    r = CliRunner().invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    assert (project / "secrets" / ".env.example").is_file()
    assert not (project / "secrets" / ".env").exists()
    assert "secrets/.env.example" in r.stdout
    assert bundle.first_run_init(project) == []
    r2 = CliRunner().invoke(app, ["init"])
    assert "복사할 설정 파일이 없습니다" in r2.stdout
