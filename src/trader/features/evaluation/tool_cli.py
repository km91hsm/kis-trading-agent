"""`trader tool submit-reflection`. 출력은 stdout JSON 1줄뿐이다(콘솔 로그는 stderr).
옵션 이름은 복기 세션 프롬프트(`agents/reflect-orchestrator.md`)가 쓰는 옛 `tool` 명령과 같다."""

from __future__ import annotations

import json
import sys
from datetime import date as date_cls

import typer

from trader.features.evaluation import tools
from trader.kernel.cli.boot import boot
from trader.kernel.cli.tool_cmd import run_tool
from trader.kernel.registry.types import ToolCommand

__all__ = ["TOOLS"]


def submit_reflection_cmd(
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
    json_text: str | None = typer.Option(
        None, "--json", help="Reflection 배열 JSON (없으면 stdin)"
    ),
) -> None:
    """Reflection 배열 제출 → 메모리 저장 + `_reflect` 체크포인트."""

    def go() -> dict:
        payload = json.loads(json_text if json_text is not None else sys.stdin.read())
        b = boot(ports=True)
        try:
            return tools.submit_reflection(
                tools.tool_ctx(b),
                market=market,
                date=date_cls.fromisoformat(date),
                payload=payload,
            )
        finally:
            if b.conn is not None:
                b.conn.close()

    run_tool(go, args={"market": market, "date": date})


TOOLS = (ToolCommand("submit-reflection", submit_reflection_cmd, ()),)
