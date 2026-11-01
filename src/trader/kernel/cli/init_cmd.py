"""`trader init`: 작업 폴더 생성과 설정 템플릿 복사(없는 파일만)."""

from __future__ import annotations

import shutil

import typer

from trader.kernel.cli.boot import config_errors
from trader.kernel.platform import bundle, paths

DIRS = ("config", "secrets", "state", "docs")


@config_errors
def init() -> None:
    """config/ secrets/ state/ docs/ 를 만들고 템플릿 중 없는 파일만 config/ 로 복사한다."""
    root = paths.project_root()
    for name in DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    src = bundle.templates_dir() / "config"
    copied: list[str] = []
    if src.is_dir():
        for f in sorted(src.glob("*.yaml")):
            dest = paths.config_dir() / f.name
            if not dest.exists():
                shutil.copyfile(f, dest)
                copied.append(f.name)
    if copied:
        typer.echo("복사한 설정 파일: " + ", ".join(copied))
    else:
        typer.echo("복사할 설정 파일이 없습니다 (이미 모두 있음)")
