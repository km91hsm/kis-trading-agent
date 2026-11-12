"""`trader tool <이름>` 루트: LLM 이 호출하는 도구 명령. 출력은 JSON 1줄뿐이다."""

from __future__ import annotations

import json
from collections.abc import Callable

import typer

from trader.kernel.registry.types import Feature, RegistryError
from trader.kernel.session import scope


def emit(payload: dict) -> None:
    """stdout 에 JSON 1줄을 쓴다. ok 가 False 면 종료 코드 1."""
    typer.echo(json.dumps(payload, ensure_ascii=False))
    if payload.get("ok") is False:
        raise typer.Exit(code=1)


def run_tool(fn: Callable[[], dict], *, args: dict | None = None) -> None:
    """fn 결과를 emit 한다. 예외는 {"ok": False, "error": "<Type>: <msg>"} 로 바꾼다.

    `args` 는 도구 인자(`ticker`·`market`·`date` …) — 세션 범위 밖이면 fn 을 돌리지 않고 거부한다
    (`scope.args_error`). 기능의 실행 헬퍼는 받은 인자를 그대로 넘긴다."""
    if args is not None and (err := scope.args_error(args, scope.current())):
        emit({"ok": False, "error": err})
        return
    try:
        payload = fn()
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001 - LLM 에는 항상 JSON 1줄을 돌려준다
        payload = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    emit(payload)


def build_tool_app(features: list[Feature]) -> typer.Typer:
    """기능이 선언한 ToolCommand 를 모은 그룹. 이름 중복은 RegistryError."""
    tool = typer.Typer(help="LLM 도구 명령 (JSON 1줄 출력)", no_args_is_help=True)

    @tool.callback()
    def _guard(ctx: typer.Context) -> None:
        """하위 명령을 돌리기 전에 세션 범위(`kernel/session/scope.py`)를 본다 — 러너가 띄운
        세션은 그 세션 종류에 허용된 도구만 부를 수 있다(러너 밖 호출은 제한 없음)."""
        if ctx.resilient_parsing or ctx.invoked_subcommand is None:
            return
        err = scope.tool_error(ctx.invoked_subcommand, scope.current())
        if err:
            emit({"ok": False, "error": err})

    seen: set[str] = set()
    for f in features:
        for tc in f.tools:
            if tc.name in seen:
                raise RegistryError(f"도구 이름 중복: {tc.name}")
            seen.add(tc.name)
            if isinstance(tc.handler, typer.Typer):
                tool.add_typer(tc.handler, name=tc.name)
            else:
                tool.command(tc.name)(tc.handler)
    return tool
