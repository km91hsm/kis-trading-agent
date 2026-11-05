"""`trader selfcheck`: 환경·원장·기능·무거운 스택 상태를 JSON 한 줄로 출력한다(번들 진단용).

원장 DB 를 만들지도 마이그레이션하지도 않는다(P11) — 파일이 있을 때만 버전을 읽는다."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import typer

from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.ledger import db
from trader.kernel.platform import bundle, paths

HEAVY_MODULES = (
    "pandas",
    "numpy",
    "sklearn",
    "pyarrow",
    "fastapi",
    "uvicorn",
    "apscheduler",
    "playwright",
    "httpx",
    "yaml",
    "pydantic",
)
# 선택 백엔드: 없어도 ok (sklearn·cem 폴백)
OPTIONAL_MODULES = ("lightgbm", "stable_baselines3")


def _ledger_version() -> int | None:
    """원장 파일이 있으면 스키마 버전, 없으면 None. sqlite3.connect 는 파일을 만들므로 먼저 본다."""
    path = paths.state_dir() / "trader.db"
    if not path.is_file():
        return None
    conn = db.connect(path)
    try:
        return db.current_version(conn)
    finally:
        conn.close()


def _import_heavy() -> tuple[list[str], list[dict[str, str]]]:
    loaded: list[str] = []
    failed: list[dict[str, str]] = []
    for name in HEAVY_MODULES:
        try:
            importlib.import_module(name)
            loaded.append(name)
        except Exception as e:  # noqa: BLE001 - 어떤 import 실패든 보고한다
            failed.append({"module": name, "error": f"{type(e).__name__}: {e}"})
    return loaded, failed


def _optional() -> dict[str, bool]:
    out: dict[str, bool] = {}
    for name in OPTIONAL_MODULES:
        try:
            importlib.import_module(name)
            out[name] = True
        except Exception:  # noqa: BLE001 - ImportError·OSError(libomp 없음) 모두 "없음"
            out[name] = False
    return out


def _playwright_driver() -> bool:
    """Playwright 드라이버(node)가 실행 가능한 파일로 있는지. 네트워크·브라우저 기동 없음."""
    try:
        from playwright._impl._driver import compute_driver_executable

        node = Path(compute_driver_executable()[0])
        return node.is_file() and os.access(node, os.X_OK)
    except Exception:  # noqa: BLE001 - 드라이버를 못 찾는 어떤 경우든 False
        return False


@config_errors
def selfcheck() -> None:
    """환경·원장 버전·기능 목록·웹 dist·무거운 스택 import·드라이버를 JSON 1줄로 출력한다.

    무거운 모듈 import 가 하나라도 실패하면 exit 1."""
    b = boot(conn=False)
    modules, failed = _import_heavy()
    info = {
        "ok": not failed,
        "python": ".".join(str(x) for x in sys.version_info[:3]),
        "root": str(paths.project_root()),
        "frozen": bundle.is_frozen(),
        "ledger_version": _ledger_version(),
        "features": [f.id for f in b.features],
        "web_dist": paths.web_dist().is_dir(),
        "modules": modules,
        "failed": failed,
        "optional": _optional(),
        "playwright_driver": _playwright_driver(),
    }
    typer.echo(json.dumps(info, ensure_ascii=False))
    if failed:
        raise typer.Exit(code=1)
