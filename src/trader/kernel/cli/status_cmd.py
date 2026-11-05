"""`trader status`: 시스템 상태를 `state/STATUS.md` 로 쓴다(새 세션이 읽는 요약)."""

from __future__ import annotations

import typer

from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.platform import paths


@config_errors
def status() -> None:
    """운영 점검·데몬·최근 실행·오늘 LLM 호출·기능 절·TODO 진행도를 STATUS.md 로 저장한다."""
    from trader.kernel.cli.main import build_command_ctx
    from trader.kernel.ops import status as ops_status

    b = boot(ports=True)
    try:
        ctx = build_command_ctx(b, echo=typer.echo)
        text = ops_status.render_markdown(ctx, todo_path=paths.docs_dir() / "TODO.md")
    finally:
        b.conn.close()
    path = ops_status.write_status(text)
    typer.echo(f"저장: {path}")
