"""`trader selfcheck`: 환경·원장·기능 상태를 JSON 한 줄로 출력한다."""

from __future__ import annotations

import json
import sys

import typer

from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.ledger import db
from trader.kernel.platform import paths


@config_errors
def selfcheck() -> None:
    """파이썬 버전·루트·원장 버전·기능 목록·웹 dist 유무를 JSON 1줄로 출력한다."""
    b = boot(conn=True)
    try:
        info = {
            "python": ".".join(str(x) for x in sys.version_info[:3]),
            "root": str(paths.project_root()),
            "ledger_version": db.current_version(b.conn),
            "features": [f.id for f in b.features],
            "web_dist": paths.web_dist().is_dir(),
        }
    finally:
        b.conn.close()
    typer.echo(json.dumps(info, ensure_ascii=False))
