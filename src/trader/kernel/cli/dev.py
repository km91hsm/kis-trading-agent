"""`trader dev`: 개발 보조 명령."""

from __future__ import annotations

from pathlib import Path

import typer

from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.platform import paths
from trader.kernel.web import gen

dev_app = typer.Typer(help="개발 보조 명령", no_args_is_help=True)


def _write_or_check(rel: Path, text: str, *, command: str, label: str, check: bool) -> None:
    """생성 파일을 쓰거나(--check 면) 최신인지만 확인한다. 다르면 종료 코드 1."""
    target = paths.project_root() / rel
    if check:
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current != text:
            msg = f"{rel.as_posix()} 가 최신이 아닙니다 (`trader dev {command}` 로 다시 만드세요)"
            typer.echo(f"오류: {msg}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"{label} 최신")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    typer.echo(f"{rel.as_posix()} 생성")


@dev_app.command("gen-sse-keys")
@config_errors
def gen_sse_keys(
    check: bool = typer.Option(False, "--check", help="쓰지 않고 최신인지만 확인한다"),
) -> None:
    """web/src/generated/sse-keys.ts 를 기능 등록부에서 다시 만든다."""
    text = gen.sse_keys_ts(boot().features)
    _write_or_check(gen.REL_PATH, text, command="gen-sse-keys", label="sse-keys", check=check)


@dev_app.command("gen-nav")
@config_errors
def gen_nav(
    check: bool = typer.Option(False, "--check", help="쓰지 않고 최신인지만 확인한다"),
) -> None:
    """web/src/generated/nav.ts 를 기능 등록부의 nav 에서 다시 만든다."""
    text = gen.nav_ts(boot().features)
    _write_or_check(gen.REL_NAV_PATH, text, command="gen-nav", label="nav", check=check)
