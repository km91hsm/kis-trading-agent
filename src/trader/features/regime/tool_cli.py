"""`trader tool` 의 regime 명령: `market {bundle,write,summarize}`·`submit-market-view`.

출력은 stdout JSON 1줄뿐이다(콘솔 로그는 stderr). 옵션 이름은 시장 세션 프롬프트
(`agents/market-orchestrator.md`)가 쓰는 옛 `tool market` 명령과 같다."""

from __future__ import annotations

import json
import sys
from datetime import date

import typer

from trader.features.regime import tools
from trader.kernel.cli.boot import boot
from trader.kernel.cli.tool_cmd import run_tool
from trader.kernel.registry.types import ToolCommand

__all__ = ["TOOLS", "market_app"]

market_app = typer.Typer(help="시장 세션 도구", no_args_is_help=True)


def _run(fn, **kwargs) -> None:
    """부트 → MarketToolContext → 도구 함수 실행 → JSON 1줄. 연결은 항상 닫는다."""

    def go() -> dict:
        b = boot(settings=True, conn=True)
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


@market_app.command("bundle")
def bundle_cmd(
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
    kind: str = typer.Option(..., "--kind", help="analyst | researcher | strategist"),
    round: int | None = typer.Option(None, "--round"),
    side: str | None = typer.Option(None, "--side"),
) -> None:
    """시장 세션 서브에이전트 입력 묶음을 JSON 으로."""
    _run(tools.market_bundle, market=market, date=date, kind=kind, round=round, side=side)


@market_app.command("write")
def write_cmd(
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
    stage: str = typer.Option(..., "--stage"),
    agent: str | None = typer.Option(None, "--agent"),
    json_text: str | None = typer.Option(None, "--json", help="페이로드 JSON (없으면 stdin)"),
) -> None:
    """`_market_analyst`·`_market_debate_r<n>_<side>` 저장(스키마·라운드 검증)."""

    def go(ctx: tools.MarketToolContext, **kw) -> dict:
        return tools.market_write(ctx, payload=_payload(json_text), **kw)

    _run(go, market=market, date=date, stage=stage, agent=agent)


@market_app.command("summarize")
def summarize_cmd(
    market: str = typer.Option(..., "--market"), date: str = typer.Option(..., "--date")
) -> None:
    """토론 턴 → `_market_summary`."""
    _run(tools.market_summarize, market=market, date=date)


def submit_cmd(
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
    json_text: str | None = typer.Option(None, "--json", help="HedgeProposal JSON (없으면 stdin)"),
) -> None:
    """전략가 제안 제출 → `_market_view` + control `market_view:<m>` + 원장 market_views."""

    def go(ctx: tools.MarketToolContext, **kw) -> dict:
        return tools.submit_market_view(ctx, payload=_payload(json_text), **kw)

    _run(go, market=market, date=date)


TOOLS = (
    ToolCommand("market", market_app, ()),
    ToolCommand("submit-market-view", submit_cmd, ()),
)
