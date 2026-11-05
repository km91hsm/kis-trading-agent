"""`trader status`: STATUS.md 생성."""

import os
import subprocess
import sys

from typer.testing import CliRunner

from trader.kernel.cli.main import app


def test_status_cli_writes_file(project):
    (project / "docs" / "TODO.md").write_text("## 단계\n- [ ] 할 일\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["status"])
    assert r.exit_code == 0, r.output
    out = project / "state" / "STATUS.md"
    assert f"저장: {out}" in r.stdout
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# 시스템 상태 (생성: ") and "## 데몬" in text and "- 할 일" in text


def test_status_cli_config_error_exit1(project):
    (project / "config" / "settings.yaml").write_text("bogus: 1\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["status"])
    assert r.exit_code == 1
    assert "오류: " in r.output and not (project / "state" / "STATUS.md").exists()


def test_status_cli_utf8_korean_path(tmp_path):
    home = tmp_path / "한글 홈"
    for name in ("config", "state", "docs", "secrets"):
        (home / name).mkdir(parents=True)
    (home / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    # 필요한 키만 넘긴다 — 개발자 셸의 WEB_TOKEN·TRADER_DRY_RUN 같은 값이 새지 않게(T6).
    # SYSTEMROOT·USERPROFILE·TEMP·TMP 는 Windows 파이썬 기동에 필요하다
    keep = ("PATH", "HOME", "SYSTEMROOT", "USERPROFILE", "TEMP", "TMP", "TMPDIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(TRADER_HOME=str(home), PYTHONIOENCODING="cp949")
    r = subprocess.run(
        [sys.executable, "-m", "trader", "status"],
        env=env, cwd=home, capture_output=True, encoding="utf-8", errors="replace",
        timeout=120, shell=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    out = home / "state" / "STATUS.md"
    assert f"저장: {out}" in r.stdout
    assert out.read_text(encoding="utf-8").startswith("# 시스템 상태 (생성: ")
