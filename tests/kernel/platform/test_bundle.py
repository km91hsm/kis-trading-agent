"""번들(PyInstaller frozen) 분기: 루트·리소스·도구 명령은 platform/bundle 한 곳이 답한다."""

import sys

import pytest

from trader.kernel.platform import bundle, paths

# 콘솔 CLI 파일명: bundle.cli_executable 이 Windows 에서는 trader.exe 를 찾는다
CLI_NAME = "trader.exe" if sys.platform == "win32" else "trader"


def _fake_home(monkeypatch, home):
    """사용자 데이터 폴더가 실제 홈에 생기지 않게 홈·LOCALAPPDATA·XDG 를 임시 폴더로 돌린다."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    return home


def _freeze(monkeypatch, exe_dir):
    (exe_dir / "_internal").mkdir(parents=True, exist_ok=True)
    _fake_home(monkeypatch, exe_dir.parent.parent / "userhome")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "trader"))
    monkeypatch.setattr(sys, "_MEIPASS", str(exe_dir / "_internal"), raising=False)
    monkeypatch.delenv("TRADER_HOME", raising=False)


def test_not_frozen_defaults(project):
    assert bundle.is_frozen() is False
    assert bundle.tool_command() == "python -m trader tool"
    assert bundle.tool_bin_dir() == paths.Path(sys.executable).parent
    assert bundle.cli_argv() == [sys.executable, "-m", "trader"]
    # 템플릿은 소스 트리 기준이라 TRADER_HOME(project)과 무관하다
    assert bundle.templates_dir().joinpath("config", "settings.yaml").is_file()
    assert bundle.templates_dir() != project / "templates"
    assert paths.web_dist() == project / "web" / "dist"


def test_frozen_root_is_user_data_dir(tmp_path, monkeypatch):
    """번들의 TRADER_HOME 은 앱 위치와 무관한 OS 사용자 데이터 폴더다(없으면 만든다)."""
    exe_dir = tmp_path / "dist" / "trader"
    _freeze(monkeypatch, exe_dir)
    monkeypatch.chdir(tmp_path)
    assert bundle.is_frozen() is True
    data = bundle.user_data_dir()
    assert data.is_relative_to(tmp_path / "userhome")
    assert not data.exists()
    assert paths.project_root() == data.resolve()
    assert data.is_dir()
    assert not (exe_dir / "config").exists()
    assert bundle.tool_command() == "trader tool"
    assert bundle.tool_bin_dir() == exe_dir.resolve()
    assert paths.web_dist() == exe_dir / "_internal" / "web" / "dist"
    assert bundle.templates_dir() == exe_dir.resolve() / "_internal" / "templates"
    assert bundle.cli_argv() == [str(exe_dir / "trader")]


def test_trader_home_still_wins_when_frozen(tmp_path, monkeypatch):
    exe_dir = tmp_path / "dist" / "trader"
    _freeze(monkeypatch, exe_dir)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TRADER_HOME", str(home))
    assert paths.project_root() == home.resolve()


def test_cli_executable_prefers_console_binary(tmp_path, monkeypatch):
    """창 없는 TraderApp 으로 떠도 세션의 `trader tool` 은 콘솔 CLI 를 쓴다."""
    exe_dir = tmp_path / "dist" / "trader"
    _freeze(monkeypatch, exe_dir)
    (exe_dir / CLI_NAME).write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(exe_dir / "TraderApp"))
    assert bundle.executable() == (exe_dir / "TraderApp").resolve()
    assert bundle.cli_executable() == (exe_dir / CLI_NAME).resolve()
    assert bundle.cli_argv() == [str((exe_dir / CLI_NAME).resolve())]


def test_cli_executable_falls_back_to_self(tmp_path, monkeypatch):
    exe_dir = tmp_path / "dist" / "trader"
    _freeze(monkeypatch, exe_dir)
    assert bundle.cli_executable() == bundle.executable()


def test_bundle_root_inside_app_bundle(tmp_path, monkeypatch):
    """macOS .app: TRADER_HOME 은 사용자 데이터 폴더(.app 옆이 아님 — /Applications 는 쓰기
    대상이 아니다), 리소스는 Contents/Frameworks(sys._MEIPASS)."""
    root = tmp_path / "x" / "trader"
    macos = root / "Trader.app" / "Contents" / "MacOS"
    frameworks = root / "Trader.app" / "Contents" / "Frameworks"
    macos.mkdir(parents=True)
    frameworks.mkdir(parents=True)
    (macos / CLI_NAME).write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(macos / "TraderApp"))
    monkeypatch.setattr(sys, "_MEIPASS", str(frameworks), raising=False)
    monkeypatch.delenv("TRADER_HOME", raising=False)
    _fake_home(monkeypatch, tmp_path / "userhome")
    data = bundle.user_data_dir().resolve()
    assert bundle.bundle_root() == data
    assert paths.project_root() == data
    assert data != root.resolve()
    assert bundle.resource_dir() == frameworks.resolve()
    assert bundle.cli_executable() == (macos / CLI_NAME).resolve()


def test_bundle_root_flat_layout_is_user_data_dir(tmp_path, monkeypatch):
    """Windows 처럼 실행파일이 폴더에 바로 있어도 데이터 루트는 실행파일 폴더가 아니다."""
    exe_dir = tmp_path / "dist" / "trader"
    _freeze(monkeypatch, exe_dir)
    assert bundle.bundle_root() == bundle.user_data_dir().resolve()
    assert bundle.bundle_root() != exe_dir.resolve()


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("darwin", ("userhome", "Library", "Application Support", "Trader")),
        ("win32", ("userhome", "AppData", "Local", "Trader")),
        ("linux", ("userhome", ".local", "share", "Trader")),
    ],
)
def test_user_data_dir_per_os(tmp_path, monkeypatch, platform, expected):
    _fake_home(monkeypatch, tmp_path / "userhome")
    monkeypatch.setattr(sys, "platform", platform)
    assert bundle.user_data_dir() == tmp_path.joinpath(*expected)


def test_user_data_dir_env_fallbacks(tmp_path, monkeypatch):
    """LOCALAPPDATA·XDG_DATA_HOME 이 없으면 홈 아래 기본 위치."""
    home = _fake_home(monkeypatch, tmp_path / "userhome")
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setattr(sys, "platform", "win32")
    assert bundle.user_data_dir() == home / "AppData" / "Local" / "Trader"
    monkeypatch.setattr(sys, "platform", "linux")
    assert bundle.user_data_dir() == home / ".local" / "share" / "Trader"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert bundle.user_data_dir() == tmp_path / "xdg" / "Trader"


def test_is_translocated():
    assert bundle.is_translocated(
        "/private/var/folders/x/T/AppTranslocation/ABC/d/Trader.app/Contents/MacOS/trader"
    )
    assert not bundle.is_translocated("/Applications/Trader.app/Contents/MacOS/trader")


def test_app_bundle_dir_is_public():
    assert hasattr(bundle, "app_bundle_dir")


def test_service_and_watchdog_argv(tmp_path, monkeypatch):
    """로그온 등록 대상은 데몬 + 웹 UI 한 프로세스(`app --no-browser`, FR-2)."""
    assert bundle.service_argv() == [sys.executable, "-m", "trader", "app", "--no-browser"]
    assert bundle.watchdog_argv() == [sys.executable, "-m", "trader", "watchdog"]
    exe_dir = tmp_path / "dist" / "trader"
    _freeze(monkeypatch, exe_dir)
    assert bundle.service_argv() == [str(exe_dir / "trader"), "app", "--no-browser"]
    assert bundle.watchdog_argv() == [str(exe_dir / "trader"), "watchdog"]


def test_icons_dir_dev_and_frozen(project, tmp_path, monkeypatch):
    assert bundle.icons_dir() == project / "packaging" / "icons"
    exe_dir = tmp_path / "dist" / "trader"
    _freeze(monkeypatch, exe_dir)
    assert bundle.icons_dir() == exe_dir.resolve() / "_internal" / "icons"


def test_first_run_init_copies_missing_only(tmp_path, monkeypatch):
    """폴더 4개를 만들고 템플릿 중 없는 파일만 복사한다(기존 파일 불변).
    frozen 이면 번들 docs 도."""
    tpl = tmp_path / "tpl"
    (tpl / "config").mkdir(parents=True)
    (tpl / "secrets").mkdir()
    (tpl / "config" / "settings.yaml").write_text("a: 1\n", encoding="utf-8")
    (tpl / "secrets" / ".env.example").write_text("WEB_TOKEN=\n", encoding="utf-8")
    monkeypatch.setattr(bundle, "templates_dir", lambda: tpl)
    root = tmp_path / "home"
    root.mkdir()
    (root / "config").mkdir()
    (root / "config" / "settings.yaml").write_text("mine: 1\n", encoding="utf-8")
    created = bundle.first_run_init(root)
    assert created == ["secrets/.env.example"]
    assert all((root / d).is_dir() for d in ("config", "secrets", "state", "docs"))
    assert (root / "config" / "settings.yaml").read_text(encoding="utf-8") == "mine: 1\n"
    assert not (root / "secrets" / ".env").exists()
    assert bundle.first_run_init(root) == []
    exe_dir = tmp_path / "dist" / "trader"
    _freeze(monkeypatch, exe_dir)
    (exe_dir / "_internal" / "docs").mkdir()
    (exe_dir / "_internal" / "docs" / "guide.md").write_text("# g\n", encoding="utf-8")
    assert bundle.first_run_init(root) == ["docs/guide.md"]


def test_spec_exe_names_differ_case_insensitively():
    """spec 의 EXE 둘(`trader` 콘솔·`TraderApp` 창 없음)은 대소문자를 무시해도 달라야 한다 —
    macOS·Windows 의 대소문자 무시 파일시스템에서 같은 폴더에 놓이면 서로 덮어쓴다."""
    import re
    from pathlib import Path

    spec = Path(__file__).resolve().parents[3] / "packaging" / "trader.spec"
    text = spec.read_text(encoding="utf-8")
    blocks = re.findall(r"\bEXE\((.*?)\n\)", text, re.S)
    names = [re.search(r'name="([^"]+)"', b).group(1) for b in blocks]
    assert len(names) == 2
    assert len({n.lower() for n in names}) == 2
    # __main__ 은 실행파일명이 traderapp* 일 때만 트레이로 분기한다
    assert any(n.lower().startswith("traderapp") for n in names)
    assert "trader" in names
