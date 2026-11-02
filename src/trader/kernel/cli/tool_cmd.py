"""`trader tool <이름>` 루트: LLM 이 호출하는 도구 명령. 출력은 JSON 1줄뿐이다."""

from __future__ import annotations

import json
from collections.abc import Callable

import typer

from trader.kernel.registry.types import Feature, RegistryError


def emit(payload: dict) -> None:
    """stdout 에 JSON 1줄을 쓴다. ok 가 False 면 종료 코드 1."""
    typer.echo(json.dumps(payload, ensure_ascii=False))
    if payload.get("ok") is False:
        raise typer.Exit(code=1)


def run_tool(fn: Callable[[], dict]) -> None:
    """fn 결과를 emit 한다. 예외는 {"ok": False, "error": "<Type>: <msg>"} 로 바꾼다."""
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
