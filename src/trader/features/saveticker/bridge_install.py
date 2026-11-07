"""세이브티커 Chrome 확장 설치: 설치된 앱(콘솔 `trader`)을 Native host 로 직접 등록한다.

- 패키지는 고정 `<데이터 루트>/saveticker-extension/` — `extension/`(Chrome 이 로드하는 복사본),
  POSIX 래퍼 `native-host`, `native-host.json`, 설치 기록 `install.json`.
- macOS/POSIX 래퍼: `env TRADER_HOME=<데이터 루트> SAVETICKER_NATIVE_ROOT=<수집 폴더> <trader>`.
  Windows 번들: 등록 JSON 의 `path` 가 설치 폴더 `trader.exe` 그 자체(데이터 루트는 frozen 기본값).
- 재실행 가능: 우리 이름·우리 확장 ID 의 등록(옛 `native-runtime` 패키지 포함)은 교체, 다른 소유자의
  등록은 거부. 앱 업데이트 뒤에는 데몬 잡이 `sync` 로 확장 복사본·등록을 맞춘다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

from trader.features.saveticker.settings import load_cfg
from trader.features.saveticker.store import atomic_json, atomic_text
from trader.kernel.config import ConfigError
from trader.kernel.platform import bundle, paths

HOST = "com.trader.saveticker"
EXTENSION_ID = "gfchfnjghmjiidifohjglpodaakdphho"
ORIGIN = f"chrome-extension://{EXTENSION_ID}/"
PERMISSIONS = {"alarms", "storage", "nativeMessaging", "downloads"}
PACKAGE_DIR = "saveticker-extension"
_REG_KEY = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST}"


class InstallError(RuntimeError):
    """고정된 설치 오류 코드. 자격 증명 내용은 절대 담지 않는다."""


def extension_source() -> Path:
    """확장 폴더 위치. 번들 리소스 → (비번들이면) 저장소 루트 순으로 찾는다."""
    candidates = [bundle.resource_dir() / "extensions" / "saveticker"]
    if not bundle.is_frozen():
        candidates.append(Path(__file__).resolve().parents[4] / "extensions" / "saveticker")
    for candidate in candidates:
        if (candidate / "manifest.json").is_file():
            return candidate
    raise InstallError("extension_resources_missing")


def _manifest(folder: Path) -> dict:
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        raw = base64.b64decode(manifest["key"], validate=True)
        digest = hashlib.sha256(raw).hexdigest()[:32]
        identity = "".join(chr(97 + int(c, 16)) for c in digest)
        if identity != EXTENSION_ID:
            raise ValueError
        if set(manifest["permissions"]) != PERMISSIONS:
            raise ValueError
        if manifest["host_permissions"] != ["https://www.saveticker.com/*"]:
            raise ValueError
        if manifest["manifest_version"] != 3:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise InstallError("invalid_extension_manifest") from None
    return manifest


def collector_root(project_home: Path) -> Path:
    """수집 폴더. `project_home/config/sources.yaml` 의 saveticker.dir 을 명시 경로로 읽는다."""
    try:
        configured = load_cfg(project_home / "config" / "sources.yaml").dir
    except ConfigError:
        configured = None
    root = Path(configured) if configured else Path("state/external/saveticker")
    return (root if root.is_absolute() else project_home / root).resolve()


def package_dir(project_home: Path | None = None) -> Path:
    """고정 패키지 위치 `<데이터 루트>/saveticker-extension/` — 앱 업데이트로 사라지지 않는다."""
    return (project_home or paths.project_root()).resolve() / PACKAGE_DIR


def registration_path(user_home: Path) -> Path:
    if sys.platform == "darwin":
        base = user_home / "Library/Application Support/Google/Chrome/NativeMessagingHosts"
    elif sys.platform == "win32":
        base = user_home / "AppData/Local/Trader/NativeMessagingHosts"
    else:
        base = user_home / ".config/google-chrome/NativeMessagingHosts"
    return base / f"{HOST}.json"


def _registry_value():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            try:
                return winreg.QueryValueEx(key, "")[0]
            except FileNotFoundError:
                return ""
    except FileNotFoundError:
        return None


def _write_registration(target: Path, host: dict, *, register_windows: bool = True) -> None:
    atomic_json(target, host)
    if sys.platform == "win32" and register_windows:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(target))


def _remove_registration(target: Path, *, register_windows: bool = True) -> None:
    if sys.platform == "win32" and register_windows:
        import winreg

        value = _registry_value()
        if value is not None:
            if value not in ("", str(target)):
                raise InstallError("registration_owner_mismatch")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _REG_KEY)
    target.unlink(missing_ok=True)


def _windows_registry(user_home: Path | None) -> bool:
    """HKCU 레지스트리까지 다루는지 — 실제 사용자 홈일 때만(테스트는 user_home 을 준다)."""
    return sys.platform == "win32" and user_home is None


def _unstable_location(path: Path) -> bool:
    """번들 실행파일이 임시 경로인지: Gatekeeper 번역 경로이거나 마운트한 디스크 이미지 안
    (`/Volumes/…` — 꺼내면 사라진다). 여기에 등록하면 다음 실행에서 깨진다."""
    text = Path(path).as_posix()
    return bundle.is_translocated(text) or (
        sys.platform == "darwin" and text.startswith("/Volumes/")
    )


def _check_location() -> None:
    if sys.platform == "win32" and not bundle.is_frozen():
        raise InstallError("windows_source_requires_bundle")
    if bundle.is_frozen() and _unstable_location(bundle.cli_executable()):
        raise InstallError("app_translocated")


def _host_spec(package: Path, project_home: Path, root: Path) -> tuple[Path, str | None]:
    """Chrome 이 띄울 실행 경로와 (POSIX 면) 래퍼 본문. Windows 번들은 설치 폴더 trader.exe 를
    직접 등록한다 — native_entry 가 frozen 데이터 루트 설정으로 수집 폴더를 찾는다."""
    if sys.platform == "win32":
        return bundle.cli_executable(), None
    if bundle.is_frozen():
        argv = [str(bundle.cli_executable())]  # 콘솔 trader — 창 없는 TraderApp 이 아니다
    else:
        argv = [sys.executable, "-m", "trader", "saveticker", "native-host"]
    command = shlex.join(
        ["env", f"TRADER_HOME={project_home}", f"SAVETICKER_NATIVE_ROOT={root}", *argv]
    )
    return package / "native-host", "#!/bin/sh\nexec " + command + ' "$@"\n'


def _host_json(executable: Path) -> dict:
    return {
        "name": HOST,
        "description": "SaveTicker local collector",
        "type": "stdio",
        "path": str(executable),
        "allowed_origins": [ORIGIN],
    }


def _write_host(package: Path, project_home: Path, root: Path) -> dict:
    executable, wrapper = _host_spec(package, project_home, root)
    if wrapper is not None:
        atomic_text(executable, wrapper)
        executable.chmod(0o700)
    host = _host_json(executable)
    atomic_json(package / "native-host.json", host)
    return host


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _ours(registration: dict | None) -> bool:
    """우리 이름·우리 확장 ID 의 등록인지(옛 native-runtime 패키지 등록 포함)."""
    return (
        registration is not None
        and registration.get("name") == HOST
        and registration.get("allowed_origins") == [ORIGIN]
    )


def _version(folder: Path) -> str | None:
    value = (_read_json(folder / "manifest.json") or {}).get("version")
    return value if isinstance(value, str) else None


def _copy_extension(source: Path, package: Path) -> Path:
    """`extension/` 을 새 복사본으로 바꾼다: 옆 폴더에 다 복사·검증한 뒤 이름 바꾸기 두 번(반쯤
    복사된 폴더를 Chrome 이 보지 않게)."""
    extension = package / "extension"
    fresh, old = package / ".extension-new", package / ".extension-old"
    for leftover in (fresh, old):
        shutil.rmtree(leftover, ignore_errors=True)
    shutil.copytree(source, fresh, ignore=shutil.ignore_patterns("tests", "node_modules"))
    _manifest(fresh)
    if extension.exists():
        extension.rename(old)
    fresh.rename(extension)
    shutil.rmtree(old, ignore_errors=True)
    return extension


def _next_steps(extension: Path) -> list[str]:
    return [
        "Chrome 주소창에 chrome://extensions 를 연다",
        "오른쪽 위 '개발자 모드' 를 켠다",
        "다른 경로로 로드된 옛 Trader 수집 확장이 있으면 먼저 '삭제' 한다",
        f"'압축해제된 확장 프로그램 로드' 로 {extension} 폴더를 고른다",
        "확장 팝업에서 '시작' 을 누른다",
    ]


def _restore(registration: Path, previous: str | None, windows: bool, previous_registry) -> None:
    """실패한 설치를 되돌린다(이전 등록이 있었으면 그 내용으로, 없었으면 지운다)."""
    try:
        if previous is None:
            registration.unlink(missing_ok=True)
        else:
            atomic_text(registration, previous)
        if windows:
            import winreg

            if previous_registry is None:
                if _registry_value() is not None:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _REG_KEY)
            else:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, previous_registry)
    except OSError:
        pass


def install(*, project_home: Path | None = None, user_home: Path | None = None) -> dict:
    """확장 복사 + Native host 등록을 한 번에(재실행 가능). 우리 등록(옛 패키지 포함)은 교체하고,
    다른 소유자의 등록이면 거부한다."""
    _check_location()
    project_home = (project_home or paths.project_root()).resolve()
    package = package_dir(project_home)
    root = collector_root(project_home)
    registration = registration_path(user_home or Path.home())
    config = root / "runtime/bridge-config.json"
    windows = _windows_registry(user_home)
    previous = registration.read_text(encoding="utf-8") if registration.exists() else None
    previous_config = config.read_text(encoding="utf-8") if config.exists() else None
    previous_registry = _registry_value() if windows else None
    if previous is not None and not _ours(_read_json(registration)):
        raise InstallError("registration_exists")
    if previous_config is not None:
        installed = _read_json(config) or {}
        if installed.get("backend") != "extension" or installed.get("extension_id") not in (
            None,
            EXTENSION_ID,
        ):
            raise InstallError("registration_exists")
    if previous_registry not in (None, "", str(registration)):
        raise InstallError("registration_exists")
    source = extension_source()
    _manifest(source)
    package.mkdir(parents=True, exist_ok=True, mode=0o700)
    extension = _copy_extension(source, package)
    host = _write_host(package, project_home, root)
    try:
        _write_registration(registration, host, register_windows=windows)
        atomic_json(
            config,
            {
                "backend": "extension",
                "extension_id": EXTENSION_ID,
                "package": str(package),
                "registration": str(registration),
            },
        )
        atomic_json(
            package / "install.json",
            {
                "schema_version": 2,
                "extension_id": EXTENSION_ID,
                "project_home": str(project_home),
                "collector_root": str(root),
                "registration": str(registration),
            },
        )
    except Exception:
        _restore(registration, previous, windows, previous_registry)
        if previous_config is None:
            config.unlink(missing_ok=True)
        else:
            atomic_text(config, previous_config)
        raise InstallError("registration_failed") from None
    return {
        "ok": True,
        "package": str(package),
        "extension_dir": str(extension),
        "extension_version": _version(extension),
        "registration": str(registration),
        "host_path": host["path"],
        "replaced": previous is not None or previous_config is not None,
        "backend": "extension",
        "next_steps": _next_steps(extension),
    }


def _installed_root(project_home: Path) -> Path:
    """설치 때 기록한 수집 폴더(설정이 바뀐 뒤에도 그 폴더의 등록을 다룬다)."""
    stored = (_read_json(package_dir(project_home) / "install.json") or {}).get("collector_root")
    return Path(stored) if isinstance(stored, str) else collector_root(project_home)


def uninstall(*, project_home: Path | None = None, user_home: Path | None = None) -> dict:
    """우리 Native host 등록만 지운다(확장 복사본은 남긴다 — Chrome 에서 직접 제거)."""
    project_home = (project_home or paths.project_root()).resolve()
    root = _installed_root(project_home)
    registration = registration_path(user_home or Path.home())
    config = root / "runtime/bridge-config.json"
    installed = _read_json(config)
    if installed is None or installed.get("backend") != "extension":
        raise InstallError("not_installed")
    from trader.kernel.daemon.lock import Lock

    # 활성 네이티브 호스트는 먼저 팝업에서 연결을 끊어야 한다.
    if Lock(root / "runtime/bridge-host.lock").held():
        raise InstallError("stop_extension_first")
    if registration.exists() and not _ours(_read_json(registration)):
        raise InstallError("registration_owner_mismatch")
    _remove_registration(registration, register_windows=_windows_registry(user_home))
    config.unlink(missing_ok=True)
    (package_dir(project_home) / "install.json").unlink(missing_ok=True)
    return {"ok": True, "backend": None, "package": str(package_dir(project_home))}


def install_status(*, project_home: Path | None = None, user_home: Path | None = None) -> dict:
    """등록 여부·등록 JSON 의 path·설치된 확장 버전 vs 번들 확장 버전·업데이트 필요 여부."""
    project_home = (project_home or paths.project_root()).resolve()
    package = package_dir(project_home)
    registration = registration_path(user_home or Path.home())
    host = _read_json(registration)
    installed = _version(package / "extension")
    try:
        bundled = _version(extension_source())
    except InstallError:
        bundled = None
    return {
        "registered": _ours(host),
        "registration": str(registration),
        "host_path": (host or {}).get("path"),
        "package": str(package),
        "installed_version": installed,
        "bundled_version": bundled,
        "update_needed": installed is not None and bundled is not None and installed != bundled,
    }


def sync(*, project_home: Path | None = None, user_home: Path | None = None) -> dict:
    """앱 업데이트 뒤 자가 복구(데몬 잡 첫 틱): 설치된 확장 버전이 번들과 다르면 복사본을 교체하고,
    등록이 현재 실행파일을 가리키지 않으면(앱 위치 변경) 다시 맞춘다. 같으면 아무것도 안 한다.
    다른 소유자의 등록이나 임시 경로(번역·디스크 이미지)에서 돈 앱이면 등록은 건드리지 않는다."""
    project_home = (project_home or paths.project_root()).resolve()
    package = package_dir(project_home)
    if _read_json(package / "install.json") is None or not (package / "extension").is_dir():
        return {"skipped": "not_installed"}
    source = extension_source()
    bundled = _version(source)
    extension_updated = _version(package / "extension") != bundled
    if extension_updated:
        _copy_extension(source, package)
    host_updated = False
    stable = sys.platform != "win32" or bundle.is_frozen()
    if stable and not (bundle.is_frozen() and _unstable_location(bundle.cli_executable())):
        root = _installed_root(project_home)
        registration = registration_path(user_home or Path.home())
        current = _read_json(registration)
        executable, wrapper = _host_spec(package, project_home, root)
        stale_wrapper = wrapper is not None and (
            not executable.is_file() or executable.read_text(encoding="utf-8") != wrapper
        )
        if (current is None or _ours(current)) and (
            stale_wrapper or current != _host_json(executable)
        ):
            host = _write_host(package, project_home, root)
            _write_registration(
                registration, host, register_windows=_windows_registry(user_home)
            )
            host_updated = True
    return {
        "extension_updated": extension_updated,
        "host_updated": host_updated,
        "version": bundled,
    }


def native_entry(origin: str) -> int:
    """네이티브 진입은 CLI 로깅을 거치지 않고 브로커·데몬 명령을 실행하지 않는다. 수집 폴더는
    래퍼의 SAVETICKER_NATIVE_ROOT → 없으면(Windows 직접 등록) 데이터 루트 설정(`paths.home()`)."""
    from trader.features.saveticker import native
    from trader.features.saveticker.paths import home

    selected_root = os.environ.get("SAVETICKER_NATIVE_ROOT")
    root = Path(selected_root) if selected_root else home()
    try:
        config = json.loads((root / "runtime/bridge-config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 1
    if config.get("backend") != "extension" or config.get("extension_id") != EXTENSION_ID:
        return 1
    result = native.serve(root, EXTENSION_ID, origin)
    return result if isinstance(result, int) else 0


def is_native_origin(value: str) -> bool:
    return re.fullmatch(r"chrome-extension://[a-p]{32}/", value) is not None
