"""`trader init`: 작업 폴더 생성과 템플릿 복사(없는 파일만, `.env` 는 만들지 않음)."""

from __future__ import annotations

import typer

from trader.kernel.cli.boot import config_errors
from trader.kernel.platform import bundle, paths


@config_errors
def init() -> None:
    """config/ secrets/ state/ docs/ 를 만들고 템플릿(config yaml·secrets/.env.example) 중
    없는 파일만 복사한다."""
    copied = bundle.first_run_init(paths.project_root())
    if copied:
        typer.echo("복사한 설정 파일: " + ", ".join(copied))
    else:
        typer.echo("복사할 설정 파일이 없습니다 (이미 모두 있음)")
