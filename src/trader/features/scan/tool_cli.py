"""`trader tool` 의 scan 명령: `scan discovery-inputs`·`submit-discovery`·`submit-screen`.

출력은 stdout JSON 1줄뿐이다(콘솔 로그는 stderr). 옵션 이름은 스캔 세션 프롬프트
(`agents/scan-orchestrator.md`)가 쓰는 옛 `tool` 명령과 같다."""

from __future__ import annotations

import json
import sys
from datetime import date

import typer

from trader.features.scan import tools
from trader.kernel.cli.boot import boot
from trader.kernel.cli.tool_cmd import run_tool
from trader.kernel.registry.types import ToolCommand

__all__ = ["TOOLS", "scan_tool_app"]

scan_tool_app = typer.Typer(help="scan 세션 도구 (Phase 19 발굴)", no_args_is_help=True)


def _run(fn, **kwargs) -> None:
    """부트(포트 조립) → ScanToolContext → 도구 함수 실행 → JSON 1줄. 연결은 항상 닫는다."""

    def go() -> dict:
        b = boot(ports=True)
        try:
            ctx = tools.tool_ctx(b)
            if isinstance(kwargs.get("date"), str):
                kwargs["date"] = date.fromisoformat(kwargs["date"])
            return fn(ctx, **kwargs)
        finally:
            if b.conn is not None:
                b.conn.close()

    run_tool(go)


def _payload(json_text: str | None) -> dict:
    return json.loads(json_text if json_text is not None else sys.stdin.read())


@scan_tool_app.command("discovery-inputs")
def discovery_inputs_cmd(
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
) -> None:
    """scout 입력(`_discovery_inputs`) JSON. 없으면 ok=false."""
    _run(tools.discovery_inputs, market=market, date=date)


def submit_discovery_cmd(
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
    json_text: str | None = typer.Option(None, "--json", help="Discovered JSON (없으면 stdin)"),
) -> None:
    """Discovered 제출 → 코드 확정·병합 후 screener 입력 후보 반환."""

    def go(ctx: tools.ScanToolContext, **kw) -> dict:
        return tools.submit_discovery(ctx, payload=_payload(json_text), **kw)

    _run(go, market=market, date=date)


def submit_screen_cmd(
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
    json_text: str | None = typer.Option(None, "--json", help="Screened JSON (없으면 stdin)"),
) -> None:
    """Screened 제출 → `_screen` 저장."""

    def go(ctx: tools.ScanToolContext, **kw) -> dict:
        return tools.submit_screen(ctx, payload=_payload(json_text), **kw)

    _run(go, market=market, date=date)


TOOLS = (
    ToolCommand("scan", scan_tool_app, ()),
    ToolCommand("submit-discovery", submit_discovery_cmd, ()),
    ToolCommand("submit-screen", submit_screen_cmd, ()),
)
