"""packaging/build.py·trader.spec·pyproject 빌드 그룹 검증.

실제 PyInstaller 빌드는 태그 CI 에서만 한다(P18).

`packaging/` 은 파이썬 패키지가 아니므로 build.py 는 파일 경로로 로드한다."""

import importlib.util
import os
import re
import subprocess
import sys
import tomllib
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


def test_assemble_replaces_only_bundle_entries_and_dmg_skips_user_data(tmp_path):
    """옛 휴대용 폴더(dist/trader = TRADER_HOME)여도 번들 파일만 바꾸고 dmg 스테이징에는
    운용 데이터가 없다."""
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
    stage = build.stage_dmg(out, tmp_path / "dmg-stage")
    assert sorted(p.name for p in stage.iterdir()) == ["Applications", "Trader.app"]


def test_spec_declares_feature_agents_and_extensions():
    """기능 agents md 는 `_internal/trader/features/<f>/agents/` 로(모듈의 `Path(__file__).parent`
    기준), 세이브티커 확장은 `tests/` 를 빼고, 웹 dist 는 있으면 들어가야 한다."""
    text = (ROOT / "packaging" / "trader.spec").read_text(encoding="utf-8")
    # 기능 README 도 함께 — boot() 의 계약 검사가 features/<f>/README.md 를 요구해 없으면 번들의
    # 모든 명령이 「기능 X: README.md 가 없습니다」 로 죽는다(v0.2.0 첫 CI 빌드에서 확인)
    includes = '["**/agents/*.md", "features/*/README.md"]'
    assert f'collect_data_files("trader", includes={includes})' in text
    assert list((ROOT / "src" / "trader" / "features").glob("*/README.md"))
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


def test_workflow_uploads_installers():
    """워크플로 업로드·릴리스 패턴이 설치 파일 이름(`<dist_name>.dmg`·`<dist_name>-setup.exe`)과
    맞다."""
    wf = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert wf.count("dist/trader-*.dmg") == 2 and wf.count("dist/trader-*-setup.exe") == 2
    assert ".zip" not in wf
    assert "choco install innosetup" in wf


def _fake_app(dist: Path) -> None:
    macos = dist / "Trader.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "TraderApp").write_text("app", encoding="utf-8")
    fw = dist / "Trader.app" / "Contents" / "Frameworks"
    fw.mkdir()
    (fw / "real.dylib").write_text("lib", encoding="utf-8")
    os.symlink("real.dylib", fw / "link.dylib")
    os.symlink(Path("Trader.app") / "Contents" / "MacOS" / "trader", dist / "trader")
    for d in ("config", "secrets", "state", "docs"):
        (dist / d).mkdir()
        (dist / d / "x").write_text("data", encoding="utf-8")


def test_stage_dmg_holds_only_app_and_applications_link(tmp_path):
    build = _load_build()
    dist = tmp_path / "trader"
    _fake_app(dist)
    stage = tmp_path / "stage"
    (stage / "stale").mkdir(parents=True)  # 지난 빌드 찌꺼기는 지운다
    build.stage_dmg(dist, stage)
    assert sorted(p.name for p in stage.iterdir()) == ["Applications", "Trader.app"]
    link = stage / "Applications"
    assert link.is_symlink() and os.readlink(link) == "/Applications"
    copied = stage / "Trader.app" / "Contents" / "Frameworks" / "link.dylib"
    assert copied.is_symlink() and os.readlink(copied) == "real.dylib"


def test_make_dmg_calls_hdiutil_with_stage(tmp_path):
    build = _load_build()
    dist = tmp_path / "dist" / "trader"
    _fake_app(dist)
    stage = tmp_path / "build" / "dmg-stage"
    seen = {}

    def fake_run(cmd):
        seen["cmd"] = cmd
        seen["entries"] = sorted(p.name for p in Path(cmd[cmd.index("-srcfolder") + 1]).iterdir())
        Path(cmd[-1]).write_text("dmg", encoding="utf-8")

    out = build.make_dmg(dist, "trader-0.3.0-macos-x86_64", stage=stage, run=fake_run)
    assert out == tmp_path / "dist" / "trader-0.3.0-macos-x86_64.dmg"
    cmd = seen["cmd"]
    assert cmd[:2] == ["hdiutil", "create"]
    assert cmd[cmd.index("-volname") + 1] == "Trader"
    assert cmd[cmd.index("-format") + 1] == "UDZO"
    assert "-ov" in cmd and cmd[-1] == str(out)
    assert seen["entries"] == ["Applications", "Trader.app"]
    assert not stage.exists()  # 스테이징은 치운다


def test_make_setup_passes_version_and_output(tmp_path):
    build = _load_build()
    dist = tmp_path / "dist" / "trader"
    dist.mkdir(parents=True)
    seen = {}

    def fake_run(cmd):
        seen["cmd"] = cmd
        (tmp_path / "dist" / "trader-0.3.0-windows-x86_64-setup.exe").write_text(
            "x", encoding="utf-8"
        )

    out = build.make_setup(
        dist, "trader-0.3.0-windows-x86_64", "0.3.0", iscc=Path("ISCC.exe"), run=fake_run
    )
    assert out.name == "trader-0.3.0-windows-x86_64-setup.exe"
    cmd = seen["cmd"]
    assert cmd[0] == "ISCC.exe" and cmd[-1] == str(ROOT / "packaging" / "trader.iss")
    assert "/DMyVersion=0.3.0" in cmd
    assert f"/DBundleDir={dist}" in cmd
    assert f"/O{tmp_path / 'dist'}" in cmd
    assert "/Ftrader-0.3.0-windows-x86_64-setup" in cmd


def test_make_setup_fails_when_no_output(tmp_path):
    build = _load_build()
    with pytest.raises(SystemExit):
        build.make_setup(
            tmp_path / "trader", "n", "0.3.0", iscc=Path("ISCC.exe"), run=lambda c: None
        )


def test_find_iscc_order(tmp_path):
    """PATH(iscc → ISCC.exe) → %ProgramFiles(x86)% → %ProgramFiles%, 없으면 SystemExit."""
    build = _load_build()
    x86 = tmp_path / "x86"
    pf = tmp_path / "pf"
    for base in (x86, pf):
        (base / "Inno Setup 6").mkdir(parents=True)
        (base / "Inno Setup 6" / "ISCC.exe").write_text("", encoding="utf-8")
    env = {"ProgramFiles(x86)": str(x86), "ProgramFiles": str(pf)}
    asked = []

    def which_iscc_upper(name):
        asked.append(name)
        return "/p/ISCC.exe" if name == "ISCC.exe" else None

    assert build.find_iscc(env, which=which_iscc_upper) == Path("/p/ISCC.exe")
    assert asked == ["iscc", "ISCC.exe"]
    assert build.find_iscc(env, which=lambda n: "/p/iscc" if n == "iscc" else None) == Path(
        "/p/iscc"
    )
    none = lambda n: None  # noqa: E731
    assert build.find_iscc(env, which=none) == x86 / "Inno Setup 6" / "ISCC.exe"
    assert (
        build.find_iscc({"ProgramFiles": str(pf)}, which=none) == pf / "Inno Setup 6" / "ISCC.exe"
    )
    with pytest.raises(SystemExit, match="Inno Setup"):
        build.find_iscc({}, which=none)


def test_make_installer_rejects_other_platforms(tmp_path):
    build = _load_build()
    with pytest.raises(SystemExit):
        build.make_installer(tmp_path, "n", "0.3.0", sys_platform="linux")


def _iss() -> str:
    raw = (ROOT / "packaging" / "trader.iss").read_bytes()
    assert raw.startswith(
        b"\xef\xbb\xbf"
    )  # 한글 주석 — Inno Setup 은 BOM 이 있어야 UTF-8 로 읽는다
    return raw.decode("utf-8-sig")


def _directive(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"{key} 지시자가 없습니다")


def test_iss_core_directives():
    text = _iss()
    assert _directive(text, "PrivilegesRequired") == "lowest"
    assert _directive(text, "DefaultDirName") == "{localappdata}\\Programs\\Trader"
    assert _directive(text, "AppName") == "Trader"
    assert _directive(text, "AppVersion") == "{#MyVersion}"
    assert _directive(text, "CloseApplications") == "yes"
    assert _directive(text, "DisableProgramGroupPage") == "yes"
    assert _directive(text, "UninstallDisplayIcon") == "{app}\\TraderApp.exe"
    assert _directive(text, "SetupIconFile") == "icons\\trader.ico"
    assert (ROOT / "packaging" / "icons" / "trader.ico").exists()
    # AppId 는 고정 GUID(바뀌면 업데이트가 다른 앱이 된다)
    assert re.fullmatch(
        r"\{\{[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}\}", _directive(text, "AppId")
    )
    assert "{userprograms}\\Trader" in text and "TraderApp.exe" in text
    assert "postinstall nowait skipifsilent" in text


def test_iss_excludes_user_data_and_keeps_data_folder():
    text = _iss()
    files = next(line for line in text.splitlines() if line.startswith("Source:"))
    assert "recursesubdirs" in files
    excludes = files.split('Excludes: "', 1)[1].split('"', 1)[0].split(",")
    for name in ("config", "secrets", "state", "docs"):
        assert f"\\{name}\\" in excludes  # 최상위만 — _internal\\templates\\secrets 는 번들 리소스
    # 제거·업데이트가 데이터 폴더(%LOCALAPPDATA%\\Trader)를 지우지 않는다
    assert "[UninstallDelete]" not in text
    for line in text.splitlines():
        if line.startswith(("Type:", "Name:")) and "filesandordirs" in line:
            assert "{app}" in line and "{localappdata}\\Trader" not in line
    assert '{localappdata}\\Trader"' not in text
    run = text.split("[UninstallRun]", 1)[1]
    assert "install --uninstall" in run and "saveticker extension uninstall" in run
