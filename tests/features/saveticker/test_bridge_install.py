import json
from pathlib import Path

import pytest

from trader.features.saveticker import bridge_install

EXTENSION_ID = "gfchfnjghmjiidifohjglpodaakdphho"


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    import sys

    if sys.platform == "win32":
        from trader.kernel.platform import bundle

        executable = tmp_path / "bundle/trader.exe"
        executable.parent.mkdir()
        executable.write_bytes(b"synthetic native executable")
        source = Path(__file__).resolve().parents[3]
        monkeypatch.setattr(bundle, "is_frozen", lambda: True)
        monkeypatch.setattr(bundle, "app_bundle_dir", lambda: None)
        monkeypatch.setattr(bundle, "cli_executable", lambda: executable)
        monkeypatch.setattr(bundle, "resource_dir", lambda: source)
    project = tmp_path / "project"
    project.mkdir()
    destination = tmp_path / "package"
    result = bridge_install.prepare(destination, project_home=project)
    return project, destination, result


def test_prepare_is_reviewable_without_registering_or_activating(prepared, tmp_path):
    project, destination, result = prepared
    assert result["extension_id"] == EXTENSION_ID
    manifest = json.loads((destination / "extension/manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["permissions"]) == {"alarms", "storage", "nativeMessaging", "downloads"}
    assert manifest["host_permissions"] == ["https://www.saveticker.com/*"]
    host = json.loads((destination / "native-host.json").read_text(encoding="utf-8"))
    assert host["allowed_origins"] == [f"chrome-extension://{EXTENSION_ID}/"]
    assert Path(host["path"]).is_file()
    assert not (project / "state/external/saveticker/runtime/bridge-config.json").exists()
    assert not (tmp_path / "user/Library").exists()


def test_install_and_uninstall_register_native_host(prepared, tmp_path):
    project, destination, _ = prepared
    user = tmp_path / "user"
    result = bridge_install.install_host(destination, user_home=user)
    assert result["ok"] is True
    registration = Path(result["registration"])
    assert registration.is_file()
    assert json.loads(registration.read_text(encoding="utf-8"))["allowed_origins"] == [
        f"chrome-extension://{EXTENSION_ID}/"
    ]
    root = project / "state/external/saveticker"
    assert (
        json.loads((root / "runtime/bridge-config.json").read_text(encoding="utf-8"))["backend"]
        == "extension"
    )
    assert bridge_install.uninstall_host(destination, user_home=user)["ok"] is True
    assert not registration.exists()
    assert not (project / "secrets").exists()  # 계정 파일 개념 없음 — secrets/ 를 건드리지 않는다
    assert not (root / "runtime/bridge-config.json").exists()


def test_registration_failure_restores_mode(prepared, tmp_path, monkeypatch):
    project, destination, _ = prepared

    def fail(*args, **kwargs):
        raise OSError("synthetic failure")

    monkeypatch.setattr(bridge_install, "_write_registration", fail)
    with pytest.raises(bridge_install.InstallError, match="registration_failed"):
        bridge_install.install_host(destination, user_home=tmp_path / "user")
    assert not (project / "state/external/saveticker/runtime/bridge-config.json").exists()


def test_frozen_prepare_contains_independent_native_runtime(tmp_path, monkeypatch):
    import shutil

    from trader.kernel.platform import bundle

    source = Path(__file__).resolve().parents[3] / "extensions/saveticker"
    app = tmp_path / "original/Trader.app"
    exe = app / "Contents/MacOS/trader"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"synthetic packaged executable")
    monkeypatch.setattr(bundle, "is_frozen", lambda: True)
    monkeypatch.setattr(bundle, "app_bundle_dir", lambda: app)
    monkeypatch.setattr(bundle, "cli_executable", lambda: exe)
    monkeypatch.setattr(bundle, "resource_dir", lambda: source.parents[1])
    home = tmp_path / "project"
    home.mkdir()
    dest = tmp_path / "package"
    bridge_install.prepare(dest, project_home=home)
    copied = dest / "native-runtime/Trader.app/Contents/MacOS/trader"
    assert copied.read_bytes() == b"synthetic packaged executable"
    host = json.loads((dest / "native-host.json").read_text(encoding="utf-8"))
    launcher = Path(host["path"]).read_text(encoding="utf-8")
    assert str(copied) in launcher
    assert str(exe) not in launcher
    shutil.rmtree(app)
    assert copied.is_file()


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


def test_uninstall_uses_installed_root_after_config_changes(prepared, tmp_path, monkeypatch):
    project, destination, _ = prepared
    bridge_install.install_host(destination, user_home=tmp_path / "user")
    monkeypatch.setattr(bridge_install, "collector_root", lambda _: tmp_path / "new-data-root")
    result = bridge_install.uninstall_host(destination, user_home=tmp_path / "user")
    assert result["ok"] is True


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
        self.value = args[-1]
        if self.fail_set:
            raise OSError("synthetic registry failure")

    def DeleteKey(self, *args):
        self.value = None


def test_windows_registration_collision_is_not_overwritten(prepared, tmp_path, monkeypatch):
    import sys

    project, destination, _ = prepared
    registry = FakeRegistry("other-owned-manifest.json")
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(bridge_install, "registration_path", lambda _: tmp_path / "native.json")
    with pytest.raises(bridge_install.InstallError, match="registration_exists"):
        bridge_install.install_host(destination)
    assert registry.value == "other-owned-manifest.json"


def test_partial_registry_install_is_rolled_back(prepared, tmp_path, monkeypatch):
    import sys

    project, destination, _ = prepared
    registry = FakeRegistry(fail_set=True)
    monkeypatch.setitem(sys.modules, "winreg", registry)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(bridge_install, "registration_path", lambda _: tmp_path / "native.json")
    with pytest.raises(bridge_install.InstallError, match="registration_failed"):
        bridge_install.install_host(destination)
    assert registry.value is None
    assert not (tmp_path / "native.json").exists()


def test_cli_prepare_does_not_activate_backend(project):
    import sys

    from typer.testing import CliRunner

    from trader.kernel.cli.main import app

    result = CliRunner().invoke(
        app, ["saveticker", "extension", "prepare", "--destination", str(project / "package")]
    )
    if sys.platform == "win32":
        assert result.exit_code == 1
        assert json.loads(result.output)["error"] == "windows_source_requires_bundle"
        return
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["extension_id"] == EXTENSION_ID
    assert not (project / "state/external/saveticker/runtime/bridge-config.json").exists()


def test_native_origin_routes_before_cli(monkeypatch):
    """Chrome 네이티브 메시징은 argv[1] 로 확장 origin 을 준다 — CLI(로깅·typer 출력)를 거치지 않고
    native_entry 로만 간다(바이너리 프레이밍 보호)."""
    import sys

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
