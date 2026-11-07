"""`trader saveticker extension install|uninstall|status` — 설치된 앱(콘솔 `trader`)을
Native host 로 직접 등록한다. 패키지는 고정 `<데이터 루트>/saveticker-extension/`,
재실행은 우리 등록만 교체."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from trader.features.saveticker import bridge_install

EXTENSION_ID = "gfchfnjghmjiidifohjglpodaakdphho"
ORIGIN = f"chrome-extension://{EXTENSION_ID}/"
SOURCE = Path(__file__).resolve().parents[3] / "extensions/saveticker"


def _bundled_version() -> str:
    return json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))["version"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """임시 데이터 루트 + Windows 에서는 번들 흉내(소스 실행 Windows 설치는 거부되므로)."""
    if sys.platform == "win32":
        _frozen_windows_like(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    return project


def _frozen_windows_like(tmp_path, monkeypatch):
    from trader.kernel.platform import bundle

    executable = tmp_path / "Programs/Trader/trader.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic installed executable")
    monkeypatch.setattr(bundle, "is_frozen", lambda: True)
    monkeypatch.setattr(bundle, "app_bundle_dir", lambda: None)
    monkeypatch.setattr(bundle, "cli_executable", lambda: executable)
    monkeypatch.setattr(bundle, "resource_dir", lambda: SOURCE.parents[1])
    return executable


def _frozen_mac(tmp_path, monkeypatch, app_dir="Applications"):
    from trader.kernel.platform import bundle

    app = tmp_path / app_dir / "Trader.app"
    exe = app / "Contents/MacOS/trader"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"synthetic packaged executable")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(bundle, "is_frozen", lambda: True)
    monkeypatch.setattr(bundle, "app_bundle_dir", lambda: app)
    monkeypatch.setattr(bundle, "cli_executable", lambda: exe)
    monkeypatch.setattr(bundle, "resource_dir", lambda: SOURCE.parents[1])
    return exe


def _config(project):
    path = project / "state/external/saveticker/runtime/bridge-config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_install_creates_fixed_package_and_registers(home, tmp_path):
    user = tmp_path / "user"
    result = bridge_install.install(project_home=home, user_home=user)
    package = home / "saveticker-extension"
    assert result["ok"] is True and result["package"] == str(package.resolve())
    manifest = json.loads((package / "extension/manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["permissions"]) == {"alarms", "storage", "nativeMessaging", "downloads"}
    assert not (package / "extension/tests").exists()
    assert not (package / "native-runtime").exists()
    registration = Path(result["registration"])
    host = json.loads(registration.read_text(encoding="utf-8"))
    assert host["name"] == "com.trader.saveticker" and host["allowed_origins"] == [ORIGIN]
    assert Path(host["path"]).is_file()
    config = _config(home)
    assert config["backend"] == "extension" and config["extension_id"] == EXTENSION_ID
    assert config["package"] == str(package.resolve())
    # 사용자가 할 일: 확장 로드 경로가 안내에 들어 있다
    assert any(str(package.resolve() / "extension") in step for step in result["next_steps"])
    assert not (home / "secrets").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 래퍼")
def test_source_wrapper_runs_python_module(home, tmp_path):
    result = bridge_install.install(project_home=home, user_home=tmp_path / "user")
    launcher = Path(result["host_path"]).read_text(encoding="utf-8")
    assert sys.executable in launcher and "-m trader saveticker native-host" in launcher
    assert f"TRADER_HOME={home.resolve()}" in launcher


def test_frozen_mac_wrapper_points_at_installed_cli(tmp_path, monkeypatch):
    """옛 계약(원본을 native-runtime 으로 복사)을 버리고 설치된 콘솔 `trader` 를 직접 가리킨다."""
    exe = _frozen_mac(tmp_path, monkeypatch)
    project = tmp_path / "data"
    project.mkdir()
    result = bridge_install.install(project_home=project, user_home=tmp_path / "user")
    package = project / "saveticker-extension"
    assert not (package / "native-runtime").exists()
    launcher = Path(result["host_path"]).read_text(encoding="utf-8")
    assert launcher.startswith("#!/bin/sh\nexec env ")
    assert str(exe) in launcher and "TraderApp" not in launcher
    assert f"TRADER_HOME={project.resolve()}" in launcher
    root = project.resolve() / "state/external/saveticker"
    assert f"SAVETICKER_NATIVE_ROOT={root}" in launcher


@pytest.mark.parametrize(
    "location",
    [
        "private/var/folders/x/AppTranslocation/ABCD/d",
        "Volumes/Trader",
    ],
)
def test_translocated_or_disk_image_app_is_refused(tmp_path, monkeypatch, location):
    from trader.kernel.platform import bundle

    exe = _frozen_mac(tmp_path, monkeypatch)
    moved = Path("/" + location) / "Trader.app/Contents/MacOS/trader"
    monkeypatch.setattr(bundle, "cli_executable", lambda: moved)
    project = tmp_path / "data"
    project.mkdir()
    with pytest.raises(bridge_install.InstallError, match="app_translocated"):
        bridge_install.install(project_home=project, user_home=tmp_path / "user")
    assert not (project / "saveticker-extension").exists()
    assert exe.is_file()


def test_reinstall_replaces_own_registration_including_old_runtime_package(home, tmp_path):
    """옛 `prepare` 패키지(native-runtime)의 등록도 우리 이름·우리 ID 면 교체한다."""
    user = tmp_path / "user"
    registration = bridge_install.registration_path(user)
    old_package = tmp_path / "old-package"
    old_host = old_package / "native-host"
    old_host.parent.mkdir(parents=True)
    old_host.write_text("#!/bin/sh\nexec old\n", encoding="utf-8")
    registration.parent.mkdir(parents=True)
    registration.write_text(
        json.dumps(
            {
                "name": "com.trader.saveticker",
                "type": "stdio",
                "path": str(old_host),
                "allowed_origins": [ORIGIN],
            }
        ),
        encoding="utf-8",
    )
    runtime = home / "state/external/saveticker/runtime"
    runtime.mkdir(parents=True)
    (runtime / "bridge-config.json").write_text(
        json.dumps(
            {"backend": "extension", "extension_id": EXTENSION_ID, "package": str(old_package)}
        ),
        encoding="utf-8",
    )
    result = bridge_install.install(project_home=home, user_home=user)
    assert result["replaced"] is True
    host = json.loads(registration.read_text(encoding="utf-8"))
    assert host["path"] == result["host_path"] and host["path"] != str(old_host)
    assert _config(home)["package"] == result["package"]
    # 재실행도 성공(멱등)
    again = bridge_install.install(project_home=home, user_home=user)
    assert again["ok"] is True and again["host_path"] == result["host_path"]


def test_foreign_registration_is_not_overwritten(home, tmp_path):
    user = tmp_path / "user"
    registration = bridge_install.registration_path(user)
    registration.parent.mkdir(parents=True)
    foreign = {"name": "com.trader.saveticker", "path": "/x", "allowed_origins": ["other"]}
    registration.write_text(json.dumps(foreign), encoding="utf-8")
    with pytest.raises(bridge_install.InstallError, match="registration_exists"):
        bridge_install.install(project_home=home, user_home=user)
    assert json.loads(registration.read_text(encoding="utf-8")) == foreign
    assert not (home / "state/external/saveticker/runtime/bridge-config.json").exists()


def test_foreign_bridge_config_is_not_overwritten(home, tmp_path):
    runtime = home / "state/external/saveticker/runtime"
    runtime.mkdir(parents=True)
    (runtime / "bridge-config.json").write_text(
        json.dumps({"backend": "other"}), encoding="utf-8"
    )
    with pytest.raises(bridge_install.InstallError, match="registration_exists"):
        bridge_install.install(project_home=home, user_home=tmp_path / "user")


def test_registration_failure_restores_previous_state(home, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("synthetic failure")

    monkeypatch.setattr(bridge_install, "_write_registration", fail)
    with pytest.raises(bridge_install.InstallError, match="registration_failed"):
        bridge_install.install(project_home=home, user_home=tmp_path / "user")
    assert not (home / "state/external/saveticker/runtime/bridge-config.json").exists()
    assert not bridge_install.registration_path(tmp_path / "user").exists()


def test_uninstall_removes_only_own_registration(home, tmp_path):
    user = tmp_path / "user"
    result = bridge_install.install(project_home=home, user_home=user)
    registration = Path(result["registration"])
    assert bridge_install.uninstall(project_home=home, user_home=user)["ok"] is True
    assert not registration.exists()
    assert not (home / "state/external/saveticker/runtime/bridge-config.json").exists()
    with pytest.raises(bridge_install.InstallError, match="not_installed"):
        bridge_install.uninstall(project_home=home, user_home=user)


def test_uninstall_refuses_foreign_registration(home, tmp_path):
    user = tmp_path / "user"
    result = bridge_install.install(project_home=home, user_home=user)
    registration = Path(result["registration"])
    foreign = {"name": "com.trader.saveticker", "path": "/x", "allowed_origins": ["other"]}
    registration.write_text(json.dumps(foreign), encoding="utf-8")
    with pytest.raises(bridge_install.InstallError, match="registration_owner_mismatch"):
        bridge_install.uninstall(project_home=home, user_home=user)
    assert registration.exists()


def test_uninstall_requires_extension_stopped(home, tmp_path):
    from trader.kernel.daemon.lock import Lock

    user = tmp_path / "user"
    bridge_install.install(project_home=home, user_home=user)
    lock = Lock(home / "state/external/saveticker/runtime/bridge-host.lock")
    assert lock.acquire()
    try:
        with pytest.raises(bridge_install.InstallError, match="stop_extension_first"):
            bridge_install.uninstall(project_home=home, user_home=user)
    finally:
        lock.release()


def test_uninstall_uses_installed_root_after_config_changes(home, tmp_path, monkeypatch):
    bridge_install.install(project_home=home, user_home=tmp_path / "user")
    monkeypatch.setattr(bridge_install, "collector_root", lambda _: tmp_path / "new-data-root")
    assert bridge_install.uninstall(project_home=home, user_home=tmp_path / "user")["ok"] is True
    assert not (home / "state/external/saveticker/runtime/bridge-config.json").exists()


def test_install_status_reports_versions(home, tmp_path):
    user = tmp_path / "user"
    before = bridge_install.install_status(project_home=home, user_home=user)
    assert before["registered"] is False and before["installed_version"] is None
    assert before["bundled_version"] == _bundled_version() and before["update_needed"] is False
    result = bridge_install.install(project_home=home, user_home=user)
    after = bridge_install.install_status(project_home=home, user_home=user)
    assert after["registered"] is True and after["host_path"] == result["host_path"]
    assert after["installed_version"] == _bundled_version() and after["update_needed"] is False


def _age_package(home):
    manifest = home / "saveticker-extension/extension/manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["version"] = "0.0.1"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    (manifest.parent / "stale.js").write_text("old", encoding="utf-8")


def test_sync_replaces_outdated_extension_copy(home, tmp_path):
    user = tmp_path / "user"
    bridge_install.install(project_home=home, user_home=user)
    _age_package(home)
    assert bridge_install.install_status(project_home=home, user_home=user)["update_needed"]
    out = bridge_install.sync(project_home=home, user_home=user)
    assert out["extension_updated"] is True and out["version"] == _bundled_version()
    extension = home / "saveticker-extension/extension"
    assert not (extension / "stale.js").exists()
    assert json.loads((extension / "manifest.json").read_text(encoding="utf-8"))["version"] == (
        _bundled_version()
    )
    assert not list((home / "saveticker-extension").glob(".extension-*"))
    # 같은 버전이면 아무것도 안 한다
    again = bridge_install.sync(project_home=home, user_home=user)
    assert again["extension_updated"] is False and again["host_updated"] is False


def test_sync_without_install_does_nothing(home, tmp_path):
    out = bridge_install.sync(project_home=home, user_home=tmp_path / "user")
    assert out == {"skipped": "not_installed"}
    assert not (home / "saveticker-extension").exists()


def test_sync_realigns_host_after_app_moves(tmp_path, monkeypatch):
    _frozen_mac(tmp_path, monkeypatch)
    project = tmp_path / "data"
    project.mkdir()
    user = tmp_path / "user"
    result = bridge_install.install(project_home=project, user_home=user)
    moved = _frozen_mac(tmp_path, monkeypatch, app_dir="Moved")
    out = bridge_install.sync(project_home=project, user_home=user)
    assert out["host_updated"] is True and out["extension_updated"] is False
    launcher = Path(result["host_path"]).read_text(encoding="utf-8")
    assert str(moved) in launcher
    shutil.rmtree(tmp_path / "Applications")
    assert bridge_install.sync(project_home=project, user_home=user)["host_updated"] is False


class FakeRegistry:
    HKEY_CURRENT_USER = 1
    REG_SZ = 1

    def __init__(self, value=None, fail_set=False):
        self.value, self.fail_set = value, fail_set

    def OpenKey(self, *args):
        if self.value is None:
            raise FileNotFoundError
        return self

    def CreateKey(self, *args):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def QueryValueEx(self, *args):
        return self.value, self.REG_SZ

    def SetValueEx(self, *args):
        if self.fail_set:
            raise OSError("synthetic registry failure")
        self.value = args[-1]

    def DeleteKey(self, *args):
        self.value = None


@pytest.fixture
def windows(tmp_path, monkeypatch):
    exe = _frozen_windows_like(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    target = tmp_path / "Local/Trader/NativeMessagingHosts/com.trader.saveticker.json"
    monkeypatch.setattr(bridge_install, "registration_path", lambda _: target)
    project = tmp_path / "data"
    project.mkdir()
    return project, exe, target


def test_windows_registers_installed_exe_in_registry(windows, monkeypatch):
    project, exe, target = windows
    registry = FakeRegistry()
    monkeypatch.setitem(sys.modules, "winreg", registry)
    result = bridge_install.install(project_home=project)
    assert registry.value == str(target)
    host = json.loads(target.read_text(encoding="utf-8"))
    assert host["path"] == str(exe) == result["host_path"]
    package = project / "saveticker-extension"
    assert not (package / "native-host").exists()
    assert not list(package.rglob("native-config.json"))
    # 재실행(멱등)·제거
    assert bridge_install.install(project_home=project)["replaced"] is True
    assert bridge_install.uninstall(project_home=project)["ok"] is True
    assert registry.value is None and not target.exists()


def test_windows_foreign_registry_is_not_overwritten(windows, monkeypatch):
    project, _, _ = windows
    registry = FakeRegistry("other-owned-manifest.json")
    monkeypatch.setitem(sys.modules, "winreg", registry)
    with pytest.raises(bridge_install.InstallError, match="registration_exists"):
        bridge_install.install(project_home=project)
    assert registry.value == "other-owned-manifest.json"


def test_windows_partial_registry_install_is_rolled_back(windows, monkeypatch):
    project, _, target = windows
    registry = FakeRegistry(fail_set=True)
    monkeypatch.setitem(sys.modules, "winreg", registry)
    with pytest.raises(bridge_install.InstallError, match="registration_failed"):
        bridge_install.install(project_home=project)
    assert registry.value is None
    assert not target.exists()


def test_windows_source_run_is_refused(tmp_path, monkeypatch):
    from trader.kernel.platform import bundle

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(bundle, "is_frozen", lambda: False)
    with pytest.raises(bridge_install.InstallError, match="windows_source_requires_bundle"):
        bridge_install.install(project_home=tmp_path, user_home=tmp_path / "user")


def test_extension_status_detects_missing_host_and_uses_data_success(tmp_path):
    from datetime import UTC, datetime

    from trader.features.saveticker import automation
    from trader.features.saveticker.models import Capture
    from trader.features.saveticker.store import Store, atomic_json

    root = tmp_path / "collector"
    atomic_json(root / "runtime/bridge-config.json", {"backend": "extension"})
    atomic_json(root / "runtime/service.json", {"state": "collecting", "pid": -1})
    atomic_json(
        root / "runtime/bridge.json",
        {"state": "collecting", "pid": -1, "updated_at": datetime.now(UTC).isoformat()},
    )
    Store(root / "data").save(
        Capture(
            stream="news_all",
            page_url="https://www.saveticker.com/news",
            observed_at=datetime.now(UTC),
            items=[{"id": "a", "title": "synthetic"}],
        )
    )
    status = automation.status(root)
    assert status["service"]["state"] == "waiting_chrome"
    assert status["last_success_at"] is not None


def test_cli_install_status_uninstall(project, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from trader.kernel.cli.main import app

    monkeypatch.setattr(bridge_install.Path, "home", lambda: tmp_path / "user")
    runner = CliRunner()
    if sys.platform == "win32":
        result = runner.invoke(app, ["saveticker", "extension", "install"])
        assert result.exit_code == 1
        assert json.loads(result.output)["error"] == "windows_source_requires_bundle"
        return
    result = runner.invoke(app, ["saveticker", "extension", "install"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["package"] == str((project / "saveticker-extension").resolve())
    status = runner.invoke(app, ["saveticker", "extension", "status"])
    assert status.exit_code == 0, status.output
    data = json.loads(status.output)
    assert data["registered"] is True and data["install"]["registered"] is True
    removed = runner.invoke(app, ["saveticker", "extension", "uninstall"])
    assert removed.exit_code == 0, removed.output
    for gone in ("prepare", "install-host", "uninstall-host"):
        assert runner.invoke(app, ["saveticker", "extension", gone]).exit_code != 0


def test_native_origin_routes_before_cli(monkeypatch):
    """Chrome 네이티브 메시징은 argv[1] 로 확장 origin 을 준다 — CLI(로깅·typer 출력)를 거치지 않고
    native_entry 로만 간다(바이너리 프레이밍 보호)."""
    from trader import __main__ as entry
    from trader.kernel.cli import main as main_mod

    origin = "chrome-extension://" + "a" * 32 + "/"
    assert bridge_install.is_native_origin(origin)
    seen = []
    monkeypatch.setattr(bridge_install, "native_entry", lambda o: seen.append(o) or 0)

    def must_not_run(args=None):
        raise AssertionError("CLI 를 거치면 안 된다")

    monkeypatch.setattr(main_mod, "app", must_not_run)
    monkeypatch.setattr(sys, "argv", ["trader", origin])
    entry.main()
    assert seen == [origin]
    monkeypatch.setattr(bridge_install, "native_entry", lambda o: 3)
    with pytest.raises(SystemExit) as e:
        entry.main()
    assert e.value.code == 3
