"""packaging/build.py·trader.spec·pyproject 빌드 그룹 검증.

실제 PyInstaller 빌드는 태그 CI 에서만 한다(P18).

`packaging/` 은 파이썬 패키지가 아니므로 build.py 는 파일 경로로 로드한다."""

import importlib.util
import os
import stat
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_build():
    path = ROOT / "packaging" / "build.py"
    spec = importlib.util.spec_from_file_location("trader_packaging_build", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_prints_captured_output_when_command_fails(capsys):
    """capture_output=True 로 돈 스모크(`trader.exe --help`)가 exit 1 이면 stdout/stderr 를
    출력해야 한다 — 원인 없이 CalledProcessError 만 남으면 CI 로그로 진단할 수 없다."""
    build = _load_build()
    # 자식 파이썬의 콘솔 인코딩(Windows cp949)에 걸리지 않게 ASCII 단서만 쓴다
    code = "import sys; print('OUT-CLUE'); print('ERR-CLUE', file=sys.stderr); sys.exit(3)"
    with pytest.raises(subprocess.CalledProcessError):
        build._run([sys.executable, "-c", code], capture_output=True)
    out = capsys.readouterr()
    assert "OUT-CLUE" in out.out and "ERR-CLUE" in out.err


def test_force_utf8_lets_korean_result_json_print(monkeypatch):
    """CI(Windows) 의 스크립트 stdout 은 cp1252 — 결과 JSON 의 한글(init 메시지)에서 죽는다."""
    import io
    import json

    build = _load_build()
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", errors="strict"))
    build._force_utf8()
    print(json.dumps({"init": "생성: config/accounts.yaml"}, ensure_ascii=False))
    sys.stdout.flush()
    assert "생성" in raw.getvalue().decode("utf-8")


def test_dist_name_uses_version_and_friendly_platform():
    build = _load_build()
    assert build.dist_name("0.1.3", "darwin", "arm64") == "trader-0.1.3-macos-arm64"
    assert build.dist_name("0.1.3", "darwin", "x86_64") == "trader-0.1.3-macos-x86_64"
    assert build.dist_name("0.1.3", "win32", "AMD64") == "trader-0.1.3-windows-x86_64"
    assert build.dist_name("0.2.0", "linux", "aarch64") == "trader-0.2.0-linux-arm64"


def test_project_version_reads_pyproject():
    build = _load_build()
    with (ROOT / "pyproject.toml").open("rb") as f:
        want = tomllib.load(f)["project"]["version"]
    assert build.project_version() == want


def test_make_zip_preserves_symlinks_and_includes_readme(tmp_path):
    """zip 이 심링크를 실제 파일로 풀어 넣으면 크기가 1.5배가 된다 — 링크는 링크로."""
    build = _load_build()
    dist = tmp_path / "trader"
    (dist / "Trader.app" / "Contents" / "MacOS").mkdir(parents=True)
    real = dist / "Trader.app" / "Contents" / "MacOS" / "trader"
    real.write_text("bin", encoding="utf-8")
    os.symlink("Trader.app/Contents/MacOS/trader", dist / "trader")
    readme = tmp_path / "bundle-usage.md"
    readme.write_text("# 사용법\n", encoding="utf-8")
    out = build.make_zip(dist, "trader-0.1.3-macos-arm64", readme=readme)
    assert out.name == "trader-0.1.3-macos-arm64.zip"
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "trader/README.md" in names
        info = zf.getinfo("trader/trader")
        assert stat.S_ISLNK(info.external_attr >> 16)
        assert zf.read("trader/trader").decode() == "Trader.app/Contents/MacOS/trader"
        assert zf.read("trader/Trader.app/Contents/MacOS/trader").decode() == "bin"


def test_assemble_dist_darwin_layout(tmp_path):
    """macOS: stage/Trader.app 을 stage/trader/ 안으로 옮기고 CLI 심링크를 둔다
    (COLLECT 폴더는 치운다)."""
    build = _load_build()
    dist = tmp_path / "dist"
    macos = dist / "Trader.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "trader").write_text("cli", encoding="utf-8")
    (macos / "TraderApp").write_text("app", encoding="utf-8")
    (dist / "trader" / "_internal").mkdir(parents=True)  # PyInstaller 의 COLLECT 산출물
    out = build.assemble_dist(dist, sys_platform="darwin")
    assert out == dist / "trader"
    assert (out / "Trader.app" / "Contents" / "MacOS" / "TraderApp").exists()
    link = out / "trader"
    assert link.is_symlink()
    assert link.resolve() == (out / "Trader.app" / "Contents" / "MacOS" / "trader").resolve()
    assert link.read_text(encoding="utf-8") == "cli"
    assert not (out / "_internal").exists()


def test_assemble_dist_windows_keeps_flat(tmp_path):
    build = _load_build()
    dist = tmp_path / "dist"
    (dist / "trader" / "_internal").mkdir(parents=True)
    (dist / "trader" / "trader.exe").write_text("", encoding="utf-8")
    (dist / "trader" / "TraderApp.exe").write_text("", encoding="utf-8")
    out = build.assemble_dist(dist, sys_platform="win32")
    assert out == dist / "trader"
    assert (out / "TraderApp.exe").exists() and (out / "_internal").exists()


def test_assemble_replaces_only_bundle_entries_and_zip_skips_user_data(tmp_path):
    """실행 중인 앱이 쓰는 폴더를 지우지 않고 번들 파일만 바꾸며 zip 에 운용 데이터가 없다."""
    build = _load_build()
    dist = tmp_path / "dist"
    target = dist / "trader"
    (target / "state" / "logs").mkdir(parents=True)
    (target / "state" / "logs" / "trader.log").write_text("로그", encoding="utf-8")
    (target / "secrets").mkdir()
    (target / "secrets" / ".env").write_text("KEY=placeholder", encoding="utf-8")
    (target / "Trader.app").mkdir()
    (target / "Trader.app" / "old").write_text("old", encoding="utf-8")
    new_app = dist / "Trader.app" / "Contents" / "MacOS"
    new_app.mkdir(parents=True)
    (new_app / "trader").write_text("bin", encoding="utf-8")
    out = build.assemble_dist(dist, sys_platform="darwin")
    assert (out / "state" / "logs" / "trader.log").exists()
    assert (out / "secrets" / ".env").exists()
    assert not (out / "Trader.app" / "old").exists() and (out / "trader").is_symlink()
    z = build.make_zip(out, "t")
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
    assert not any(n.startswith(("trader/secrets", "trader/state")) for n in names)
    assert any(n.startswith("trader/Trader.app") for n in names)


def test_spec_declares_feature_agents_and_extensions():
    """기능 agents md 는 `_internal/trader/features/<f>/agents/` 로(모듈의 `Path(__file__).parent`
    기준), 세이브티커 확장은 `tests/` 를 빼고, 웹 dist 는 있으면 들어가야 한다."""
    text = (ROOT / "packaging" / "trader.spec").read_text(encoding="utf-8")
    assert 'collect_data_files("trader", includes=["**/agents/*.md"])' in text
    assert "extensions/saveticker" in text
    assert "web/dist" in text
    assert '"tests"' in text  # 확장 datas 에서 tests/ 를 빼는 규칙
    assert "docs/bundle-usage.md" in text
    # 옛 저장소의 `.claude/agents` 경로는 리플랫폼에서 없어졌다
    assert ".claude" not in text
    # spec 이 가리키는 기능 agents md 가 실제로 있다
    assert list((ROOT / "src" / "trader" / "features").glob("*/agents/*.md"))


def test_build_group_has_pyinstaller():
    with (ROOT / "pyproject.toml").open("rb") as f:
        data = tomllib.load(f)
    build_group = data["dependency-groups"]["build"]
    assert any(d.startswith("pyinstaller") for d in build_group)
    assert any(d.startswith("pillow") for d in build_group)
    assert any(d.startswith("pystray") for d in data["project"]["dependencies"])
