"""번들 빌드: (웹 빌드) → PyInstaller → 스모크 → 설치 파일. 셸 스크립트 대신 파이썬.

배포 빌드는 `v*` 태그 CI(.github/workflows/build.yml)에서 돈다(P18).

사용: uv run --group build python packaging/build.py [--skip-web] [--skip-smoke]
산출: dist/trader/ (실행파일 + _internal/ 또는 Trader.app) 와 설치 파일
  - macOS: dist/trader-<버전>-macos-<arch>.dmg (Trader.app + 응용 프로그램 바로가기)
  - Windows: dist/trader-<버전>-windows-x86_64-setup.exe (Inno Setup 6, packaging/trader.iss)
데이터(config/ secrets/ state/ docs/)는 OS 사용자 데이터 폴더에 있고 설치 파일에는 들어가지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "trader"
# PyInstaller 출력은 여기에 두고 번들 항목만 DIST 로 옮긴다(--noconfirm 이 출력 폴더를 비우므로
# 운용 중인 dist/trader 를 PyInstaller 가 직접 건드리지 않게)
STAGE = ROOT / "build" / "dist-stage"


def _force_utf8() -> None:
    """스크립트 자신의 stdout/stderr 를 UTF-8 로.

    Windows CI 는 cp1252 라 결과 JSON 의 한글에서 죽는다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    try:
        return subprocess.run(
            cmd, shell=False, check=True, encoding="utf-8", errors="replace", **kw
        )
    except subprocess.CalledProcessError as e:
        # capture_output 으로 돈 명령이 실패하면 출력이 묻힌다 → CI 로그에 꼬리를 남긴다
        if e.stdout:
            print(e.stdout[-4000:], flush=True)
        if e.stderr:
            print(e.stderr[-4000:], file=sys.stderr, flush=True)
        raise


def build_web() -> None:
    web = ROOT / "web"
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        raise SystemExit(
            "npm 이 없어 웹 프런트를 빌드할 수 없습니다 (--skip-web 로 기존 dist 사용)"
        )
    if not (web / "node_modules").exists():
        _run([npm, "ci"], cwd=str(web))
    _run([npm, "run", "build"], cwd=str(web))


def assemble_dist(
    stage: Path, *, target: Path | None = None, sys_platform: str = sys.platform
) -> Path:
    """PyInstaller 산출물(stage)로 배포 폴더(target, 기본 `stage/trader`)를 만든다.

    macOS: `stage/Trader.app` 을 target 안으로 옮기고 터미널용 심링크 `trader →
    Trader.app/Contents/MacOS/trader` 를 둔다. Windows: COLLECT 폴더(`stage/trader`)의 항목을 target
    으로 옮긴다. 어느 쪽이든 target 을 통째로 지우지 않고 **번들 항목만** 바꾼다 — target 은 실행
    중인 앱의 TRADER_HOME 일 수 있다(데몬이 로그를 쓰는 중이면 rmtree 가 실패해 앱 파일만 지워진 채
    남았다, 2026-10-27). 사용자 데이터(USER_DATA)는 건드리지 않는다."""
    target = target or stage / "trader"
    target.mkdir(parents=True, exist_ok=True)
    if sys_platform != "darwin":
        collect = stage / "trader"
        if collect.resolve() != target.resolve():
            for item in sorted(collect.iterdir()):
                _remove(target / item.name)
                shutil.move(str(item), str(target / item.name))
        return target
    app = stage / "Trader.app"
    if not app.is_dir():
        raise SystemExit(f"Trader.app 이 없습니다: {app}")
    for name in BUNDLE_ENTRIES:
        _remove(target / name)
    shutil.move(str(app), str(target / "Trader.app"))
    os.symlink(Path("Trader.app") / "Contents" / "MacOS" / "trader", target / "trader")
    return target


def _remove(p: Path) -> None:
    if p.name in USER_DATA:
        return
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)


# 옛 휴대용 배포에서는 번들 폴더 = 운용 중인 TRADER_HOME 이었다(Trader.app 의 부모). 빌드는
# 이 이름들을 건드리지 않고 설치 파일에도 넣지 않는다 — 원장·키·설정이 지워지던 문제
# (전체 검토 2026-10-27)
USER_DATA = ("config", "secrets", "state", "docs")
# macOS 번들 폴더에서 빌드가 바꾸는 것(COLLECT 가 남긴 평면 산출물 포함)
BUNDLE_ENTRIES = ("Trader.app", "trader", "TraderApp", "_internal", "README.md")


def cli_exe(dist_dir: Path) -> Path:
    return dist_dir / ("trader.exe" if sys.platform == "win32" else "trader")


def internal_dir(dist_dir: Path) -> Path:
    """리소스 폴더: Windows `_internal/`, macOS `.app` 의 Contents/Frameworks (sys._MEIPASS)."""
    if sys.platform == "darwin":
        return dist_dir / "Trader.app" / "Contents" / "Frameworks"
    return dist_dir / "_internal"


def build_bundle() -> Path:
    if not (ROOT / "web" / "dist" / "index.html").exists():
        raise SystemExit("web/dist 가 없습니다. 먼저 웹을 빌드하세요 (npm run build)")
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(ROOT / "packaging" / "trader.spec"),
            "--noconfirm",
            "--distpath",
            str(STAGE),
            "--workpath",
            str(ROOT / "build" / "pyinstaller"),
        ],
        cwd=str(ROOT),
    )
    assemble_dist(STAGE, target=DIST)
    exe = cli_exe(DIST)
    if not exe.exists():
        raise SystemExit(f"실행파일이 없습니다: {exe}")
    return exe


def smoke(exe: Path) -> dict:
    """번들이 pyproject 없는 폴더에서 init·status 를 돌릴 수 있는지, 리소스가 들어갔는지 확인."""
    out: dict = {}
    _run([str(exe), "--help"], capture_output=True)
    with tempfile.TemporaryDirectory(prefix="trader-smoke-") as tmp:
        env = {**os.environ, "TRADER_HOME": tmp, "PYTHONIOENCODING": "utf-8"}
        r = _run([str(exe), "init"], capture_output=True, env=env)
        out["init"] = r.stdout.strip()[:200]
        for rel in ("config/accounts.yaml", "config/settings.yaml", "secrets/.env.example"):
            if not (Path(tmp) / rel).exists():
                raise SystemExit(f"init 뒤 {rel} 이 없습니다")
        if "00000000" not in (Path(tmp) / "config" / "accounts.yaml").read_text(encoding="utf-8"):
            raise SystemExit("accounts.yaml 템플릿에 계좌번호 자리(00000000)가 없습니다")
        r = _run([str(exe), "status"], capture_output=True, env=env)
        out["status"] = r.stdout.strip()[-120:]
        # 무거운 스택(sklearn·pyarrow·pandas·fastapi·uvicorn)이 실제로 import 되는지 —
        # --help·status 는 이들을 건드리지 않아 hidden import·dylib 누락을 못 잡는다
        r = _run([str(exe), "selfcheck"], capture_output=True, env=env)
        out["selfcheck"] = r.stdout.strip().splitlines()[-1][:200]
        _run([str(exe), "ml", "models"], capture_output=True, env=env)
        _run([str(exe), "evaluation", "rl", "models"], capture_output=True, env=env)
        if not (Path(tmp) / "state" / "STATUS.md").exists():
            raise SystemExit("status 가 STATUS.md 를 만들지 않았습니다")
    internal = internal_dir(DIST)
    for rel in (
        "templates/config/accounts.yaml",
        "templates/secrets/.env.example",
        "web/dist/index.html",
        "icons/tray-dark.png",
        # 기능 agents md: 모듈의 Path(__file__).parent / "agents" 기준
        "trader/features/spot/agents/orchestrator.md",
    ):
        if not (internal / rel).exists():
            raise SystemExit(f"번들 리소스 누락: {internal.name}/{rel}")
    # 번들 **안**(리소스 폴더)에 운용 데이터가 섞였는지만 본다 — dist/trader 바로 아래의
    # secrets·state 는 옛 휴대용 TRADER_HOME 의 사용자 데이터일 수 있다
    # (설치 파일은 USER_DATA 를 뺀다)
    for bad in ("secrets", "state"):
        if (internal / bad).exists():
            raise SystemExit(f"번들에 {bad}/ 가 들어갔습니다")
    windowed = DIST / (
        "TraderApp.exe" if sys.platform == "win32" else "Trader.app/Contents/MacOS/TraderApp"
    )
    if not windowed.exists():
        raise SystemExit(f"창 없는 실행파일이 없습니다: {windowed}")
    if sys.platform == "darwin" and not (DIST / "Trader.app" / "Contents" / "Info.plist").exists():
        raise SystemExit("Trader.app/Contents/Info.plist 가 없습니다")
    out["windowed"] = str(windowed)
    return out


PLATFORM_NAMES = {"darwin": "macos", "win32": "windows", "linux": "linux"}
ARCH_NAMES = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}


def project_version() -> str:
    """버전의 출처는 pyproject.toml 하나 (태그·설치 파일 이름·Info.plist·AppVersion)."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("pyproject.toml 에 version 이 없습니다")
    return m.group(1)


def dist_name(version: str, sys_platform: str, machine: str) -> str:
    """설치 파일 이름 바탕: trader-<버전>-<macos|windows|linux>-<arch> (+ Windows `-setup`)."""
    arch = machine.lower()
    arch = ARCH_NAMES.get(arch, arch)
    return f"trader-{version}-{PLATFORM_NAMES.get(sys_platform, sys_platform)}-{arch}"


DMG_STAGE = ROOT / "build" / "dmg-stage"
ISS = ROOT / "packaging" / "trader.iss"


def stage_dmg(dist_dir: Path, stage: Path) -> Path:
    """dmg 에 담을 폴더를 새로 만든다: `Trader.app`(심링크 보존) + `Applications → /Applications`.

    dist_dir 의 다른 항목(터미널용 `trader` 링크·USER_DATA)은 복사하지 않는다 — 원장·키·설정이
    설치 파일에 섞이지 않게(데이터는 사용자 데이터 폴더에 있다)."""
    app = dist_dir / "Trader.app"
    if not app.is_dir():
        raise SystemExit(f"Trader.app 이 없습니다: {app}")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copytree(app, stage / "Trader.app", symlinks=True)
    os.symlink("/Applications", stage / "Applications")
    return stage


def make_dmg(dist_dir: Path, name: str, *, stage: Path = DMG_STAGE, run=None) -> Path:
    """macOS 설치 디스크 이미지 `<dist>/<name>.dmg` (끌어다 놓기 설치)."""
    run = run or _run
    stage_dmg(dist_dir, stage)
    target = dist_dir.parent / f"{name}.dmg"
    if target.exists():
        target.unlink()
    run(
        [
            "hdiutil",
            "create",
            "-volname",
            "Trader",
            "-srcfolder",
            str(stage),
            "-ov",
            "-format",
            "UDZO",
            str(target),
        ]
    )
    shutil.rmtree(stage, ignore_errors=True)
    return target


def find_iscc(env: dict | None = None, which=shutil.which) -> Path:
    """Inno Setup 6 컴파일러: PATH(`iscc`·`ISCC.exe`) → `%ProgramFiles(x86)%` → `%ProgramFiles%`."""
    env = os.environ if env is None else env
    for cand in ("iscc", "ISCC.exe"):
        found = which(cand)
        if found:
            return Path(found)
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        base = env.get(var)
        if base:
            exe = Path(base) / "Inno Setup 6" / "ISCC.exe"
            if exe.is_file():
                return exe
    raise SystemExit(
        "Inno Setup 6 의 ISCC.exe 를 찾을 수 없습니다 — https://jrsoftware.org/isinfo.php 에서 "
        "설치하거나 `choco install innosetup` 후 다시 실행하세요"
    )


def make_setup(
    dist_dir: Path, name: str, version: str, *, iscc: Path | None = None, run=None
) -> Path:
    """Windows 설치 프로그램 `<dist>/<name>-setup.exe` (packaging/trader.iss, 사용자 권한)."""
    run = run or _run
    iscc = iscc or find_iscc()
    out_dir = dist_dir.parent
    base = f"{name}-setup"
    target = out_dir / f"{base}.exe"
    target.unlink(missing_ok=True)  # 지난 빌드의 산출물을 이번 것으로 착각하지 않게
    run(
        [
            str(iscc),
            "/Qp",
            f"/DMyVersion={version}",
            f"/DBundleDir={dist_dir}",
            f"/O{out_dir}",
            f"/F{base}",
            str(ISS),
        ]
    )
    if not target.exists():
        raise SystemExit(f"설치 프로그램이 만들어지지 않았습니다: {target}")
    return target


def make_installer(
    dist_dir: Path, name: str, version: str, sys_platform: str = sys.platform
) -> Path:
    if sys_platform == "darwin":
        return make_dmg(dist_dir, name)
    if sys_platform == "win32":
        return make_setup(dist_dir, name, version)
    raise SystemExit(f"설치 파일을 만들지 않는 플랫폼입니다: {sys_platform}")


def main() -> None:
    _force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-web", action="store_true", help="웹 빌드 생략 (기존 web/dist 사용)")
    ap.add_argument("--skip-smoke", action="store_true")
    ap.add_argument(
        "--check-tag",
        metavar="TAG",
        help="v<버전> 태그가 pyproject 버전과 같은지만 확인하고 끝낸다",
    )
    args = ap.parse_args()
    if args.check_tag:
        want = f"v{project_version()}"
        if args.check_tag != want:
            raise SystemExit(f"태그 {args.check_tag} 가 pyproject 버전 {want} 과 다릅니다")
        print(want)
        return
    if not args.skip_web:
        build_web()
    # 빌드는 dist/trader 의 번들 항목만 바꾸고(PyInstaller 출력은 STAGE) 사용자 데이터는 옮기지도
    # 지우지도 않는다 — 실행 중인 앱의 데몬이 같은 폴더를 쓰고 있어도 안전. 스모크는 임시
    # TRADER_HOME, 설치 파일(dmg·setup.exe)은 USER_DATA 를 담지 않는다
    exe = build_bundle()
    result = {"exe": str(exe)}
    if not args.skip_smoke:
        result["smoke"] = smoke(exe)
    version = project_version()
    name = dist_name(version, sys.platform, platform.machine())
    installer = make_installer(DIST, name, version)
    result["installer"] = str(installer)
    result["installer_name"] = installer.name
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
