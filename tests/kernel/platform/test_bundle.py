"""번들(PyInstaller frozen) 분기: 루트·리소스·도구 명령은 platform/bundle 한 곳이 답한다."""

import sys

from trader.kernel.platform import bundle, paths

# 콘솔 CLI 파일명: bundle.cli_executable 이 Windows 에서는 trader.exe 를 찾는다
CLI_NAME = "trader.exe" if sys.platform == "win32" else "trader"


def _freeze(monkeypatch, exe_dir):
    (exe_dir / "_internal").mkdir(parents=True, exist_ok=True)
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


def test_frozen_root_is_executable_dir(tmp_path, monkeypatch):
    """pyproject 가 없는 아무 폴더에 풀어도 실행파일 폴더가 TRADER_HOME 이다 (폴더째 이동 가능)."""
    exe_dir = tmp_path / "dist" / "trader"
    _freeze(monkeypatch, exe_dir)
    monkeypatch.chdir(tmp_path)
    assert bundle.is_frozen() is True
    assert paths.project_root() == exe_dir.resolve()
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
    """macOS .app: TRADER_HOME 은 .app 의 부모 폴더, 리소스는 Contents/Frameworks(sys._MEIPASS)."""
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
    assert bundle.bundle_root() == root.resolve()
    assert paths.project_root() == root.resolve()
    assert bundle.resource_dir() == frameworks.resolve()
    assert bundle.cli_executable() == (macos / CLI_NAME).resolve()


def test_bundle_root_windows_flat_layout_unchanged(tmp_path, monkeypatch):
    exe_dir = tmp_path / "dist" / "trader"
    _freeze(monkeypatch, exe_dir)
    assert bundle.bundle_root() == exe_dir.resolve()


def test_app_bundle_dir_is_public():
    assert hasattr(bundle, "app_bundle_dir")


def test_daemon_and_watchdog_argv(tmp_path, monkeypatch):
    assert bundle.daemon_argv() == [sys.executable, "-m", "trader", "daemon"]
    assert bundle.watchdog_argv() == [sys.executable, "-m", "trader", "watchdog"]
    exe_dir = tmp_path / "dist" / "trader"
    _freeze(monkeypatch, exe_dir)
    assert bundle.daemon_argv() == [str(exe_dir / "trader"), "daemon"]
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
