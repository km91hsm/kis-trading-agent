# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder 번들: 실행파일 둘 + _internal/.

- `trader`(console=True): CLI·세션의 `trader tool`·launchd 의 `trader daemon`.
- `TraderApp`(console=False): 더블클릭용 — 트레이/메뉴바 아이콘 + 데몬 + 웹. `__main__` 은 frozen 이고
  실행파일명이 `traderapp*` 이면 `trader app --tray` 로 분기한다. macOS·Windows 의 파일시스템은
  대소문자를 무시하므로 `Trader` 가 아니라 `TraderApp` (tests/kernel/platform/test_bundle.py).
- macOS 는 BUNDLE 로 `Trader.app` 을 만든다. TRADER_HOME 은 `.app` 의 부모 폴더
  (src/trader/kernel/platform/bundle.py).

_internal/(sys._MEIPASS) 리소스 레이아웃은 platform/bundle.py 가 기대하는 대로:
templates/ icons/ web/dist/ extensions/saveticker/ docs/, 기능 agents md 는 모듈 옆
trader/features/<기능>/agents/*.md (`Path(__file__).parent / "agents"`), 기능 README.md 는 boot() 계약 검사용.
secrets/·state/ 는 넣지 않는다.
실제 빌드는 v* 태그 CI 에서만 돈다(packaging/build.py, .github/workflows/build.yml)."""

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # packaging/ → 저장소 루트
VERSION = re.search(
    r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M
).group(1)
ICON = str(ROOT / "packaging" / "icons" / ("trader.icns" if sys.platform == "darwin" else "trader.ico"))

datas = [
    (str(ROOT / "templates"), "templates"),
    (str(ROOT / "packaging" / "icons"), "icons"),
]
for rel, dst in (("web/dist", "web/dist"), ("docs/bundle-usage.md", "docs")):
    if (ROOT / rel).exists():
        datas.append((str(ROOT / rel), dst))
# 세이브티커 Chrome 확장(번들의 `saveticker extension prepare` 가 복사한다). tests/ 는 빼고 파일 단위로
EXT = ROOT / "extensions/saveticker"
if EXT.is_dir():
    for f in sorted(EXT.rglob("*")):
        rel = f.relative_to(EXT)
        if f.is_file() and "tests" not in rel.parts:
            datas.append((str(f), (Path("extensions/saveticker") / rel.parent).as_posix()))
# 기능 agents md: _internal/trader/features/<기능>/agents/*.md, 기능 README: boot() 계약 검사가 요구
datas += collect_data_files("trader", includes=["**/agents/*.md", "features/*/README.md"])
datas += collect_data_files("tzdata")
# 세이브티커 수집기: Playwright 드라이버(node + package). Chromium 은 첫 실행에 내려받는다.
datas += collect_data_files("playwright")

hiddenimports = (
    collect_submodules("trader")
    + collect_submodules("sklearn")
    + collect_submodules("uvicorn")
    + collect_submodules("apscheduler")
    + collect_submodules("playwright")
    + ["pypdf"]
    + ["pyarrow", "pyarrow.parquet", "tzdata", "youtube_transcript_api", "bs4"]
    + ["PIL.Image", "PIL.PngImagePlugin"]
    + (
        ["pystray._darwin"]
        if sys.platform == "darwin"
        else ["pystray._win32"]
        if sys.platform == "win32"
        else []
    )
)

a = Analysis(
    [str(ROOT / "src" / "trader" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "lightgbm", "torch", "stable_baselines3", "gymnasium", "matplotlib", "tkinter", "IPython"
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe_cli = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="trader",
    console=True,
    icon=ICON,
    disable_windowed_traceback=False,
)
exe_app = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="TraderApp",
    console=False,
    icon=ICON,
    disable_windowed_traceback=True,
)
coll = COLLECT(exe_cli, exe_app, a.binaries, a.datas, name="trader", contents_directory="_internal")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Trader.app",
        icon=ICON,
        bundle_identifier="dev.trader.app",
        info_plist={
            "CFBundleName": "Trader",
            "CFBundleDisplayName": "Trader",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundleExecutable": "TraderApp",
            "LSUIElement": True,  # Dock 아이콘 없이 메뉴바만
            "NSHighResolutionCapable": True,
        },
    )
