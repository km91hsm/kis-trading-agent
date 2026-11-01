"""`trader dev`: 개발 보조 명령."""

from __future__ import annotations

import typer

from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.platform import paths
from trader.kernel.web import gen

dev_app = typer.Typer(help="개발 보조 명령", no_args_is_help=True)


@dev_app.command("gen-sse-keys")
@config_errors
def gen_sse_keys(
    check: bool = typer.Option(False, "--check", help="쓰지 않고 최신인지만 확인한다"),
) -> None:
    """web/src/generated/sse-keys.ts 를 기능 등록부에서 다시 만든다."""
    target = paths.project_root() / gen.REL_PATH
    text = gen.sse_keys_ts(boot().features)
    if check:
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current != text:
            msg = f"{gen.REL_PATH} 가 최신이 아닙니다 (`trader dev gen-sse-keys` 로 다시 만드세요)"
            typer.echo(f"오류: {msg}", err=True)
            raise typer.Exit(code=1)
        typer.echo("sse-keys 최신")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    typer.echo(f"{gen.REL_PATH} 생성")
