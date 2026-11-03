"""`trader tool` 의 spot 명령: status·collect·checkpoint {list,read,write,bundle,summarize}·
submit-decision.

출력은 stdout JSON 1줄뿐이다(콘솔 로그는 stderr). 옵션 이름은 LLM 세션 프롬프트가 쓰는
옛 `tool` 명령과 같다. dry_run 은 인자가 아니라 env `TRADER_DRY_RUN` 으로만 정한다(`tool_ctx`)."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime

import typer

from trader.features.spot import tools
from trader.features.spot.tools import SUBMIT_RULE, WRITE_RULES, ToolError
from trader.kernel.cli.boot import boot
from trader.kernel.cli.tool_cmd import run_tool
from trader.kernel.registry.types import ToolCommand
from trader.kernel.session import run_store

__all__ = ["SUBMIT_RULE", "TOOLS", "WRITE_RULES", "checkpoint_app"]

checkpoint_app = typer.Typer(help="체크포인트 도구", no_args_is_help=True)


def _run(fn, **kwargs) -> None:
    """부트(포트 조립) → ToolContext → 도구 함수 실행 → JSON 1줄. 연결은 항상 닫는다."""

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
    import json

    return json.loads(json_text if json_text is not None else sys.stdin.read())


def status_cmd(
    market: str = typer.Option(..., "--market"), date: str = typer.Option(..., "--date")
) -> None:
    """오늘 종목별 체크포인트 목록과 세션 수."""
    _run(tools.status, market=market, date=date)


def _collect(
    ctx: tools.ToolContext,
    *,
    ticker: str,
    market: str,
    exchange: str | None,
    asof: str | None,
    force: bool,
) -> dict:
    if ctx.inputs is None:
        raise ToolError("수집 포트 없음")
    tools.validate_ids(ticker, market)
    when = datetime.fromisoformat(asof) if asof else ctx.now()
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    payload = ctx.inputs.collect(
        ticker=ticker,
        market=market,
        exchange=exchange,
        asof=when,
        force=force,
        conn_factory=None,
        market_client=ctx.market_client,
        state=ctx.state,
    )
    when_day = run_store.run_date_for(market, when)
    d = run_store.run_dir(when_day, market, ticker, ctx.state)
    quant = tools.ensure_quant(ctx, ticker=ticker, market=market, date=when_day)
    c = tools.ensure_calibration(ctx, ticker=ticker, market=market, date=when_day)
    return {
        "ok": True,
        "path": str(d / "00_inputs.json"),
        "errors": (payload or {}).get("errors", []),
        "quant": bool(quant),
        "calibration": bool(c),
    }


def collect_cmd(
    ticker: str = typer.Option(..., "--ticker"),
    market: str = typer.Option("KR", "--market"),
    exchange: str | None = typer.Option(None, "--exchange"),
    asof: str | None = typer.Option(None, "--asof"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """00_inputs 수집 (있으면 건너뜀). JSON {ok, path, errors}."""
    _run(_collect, ticker=ticker, market=market, exchange=exchange, asof=asof, force=force)


@checkpoint_app.command("list")
def checkpoint_list_cmd(
    ticker: str = typer.Option(..., "--ticker"),
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
) -> None:
    _run(tools.checkpoint_list, ticker=ticker, market=market, date=date)


@checkpoint_app.command("read")
def checkpoint_read_cmd(
    ticker: str = typer.Option(..., "--ticker"),
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
    stage: str = typer.Option(..., "--stage"),
    section: str | None = typer.Option(None, "--section"),
) -> None:
    _run(
        tools.checkpoint_read,
        ticker=ticker,
        market=market,
        date=date,
        stage=stage,
        section=section,
    )


@checkpoint_app.command("write")
def checkpoint_write_cmd(
    ticker: str = typer.Option(..., "--ticker"),
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
    stage: str = typer.Option(..., "--stage"),
    agent: str | None = typer.Option(None, "--agent"),
    json_text: str | None = typer.Option(None, "--json", help="페이로드 JSON (없으면 stdin)"),
    model: str | None = typer.Option(None, "--model", help="서브에이전트 모델명 (call.model 기록)"),
) -> None:
    def go(ctx: tools.ToolContext, **kw) -> dict:
        return tools.checkpoint_write(ctx, payload=_payload(json_text), **kw)

    _run(
        go, ticker=ticker, market=market, date=date, stage=stage, agent=agent, model=model
    )


@checkpoint_app.command("bundle")
def checkpoint_bundle_cmd(
    ticker: str = typer.Option(..., "--ticker"),
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
    kind: str = typer.Option(..., "--kind", help="researcher | trader"),
    round: int | None = typer.Option(None, "--round"),
    side: str | None = typer.Option(None, "--side"),
) -> None:
    """서브에이전트 입력 묶음(리포트+턴 또는 리포트+요약+컨텍스트)을 JSON 으로."""
    _run(
        tools.checkpoint_bundle,
        ticker=ticker,
        market=market,
        date=date,
        kind=kind,
        round=round,
        side=side,
    )


@checkpoint_app.command("summarize")
def checkpoint_summarize_cmd(
    ticker: str = typer.Option(..., "--ticker"),
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
) -> None:
    _run(tools.checkpoint_summarize, ticker=ticker, market=market, date=date)


def submit_cmd(
    ticker: str = typer.Option(..., "--ticker"),
    market: str = typer.Option(..., "--market"),
    date: str = typer.Option(..., "--date"),
    json_text: str | None = typer.Option(None, "--json", help="Decision JSON (없으면 stdin)"),
    stage: str | None = typer.Option(None, "--stage", help="재평가면 31_decision_r<n>"),
) -> None:
    """결정 제출 → 계좌별 리스크 판정·실행(주문은 executor 코어에서만). JSON 1줄."""

    def go(ctx: tools.ToolContext, **kw) -> dict:
        return tools.submit_decision(ctx, payload=_payload(json_text), **kw)

    _run(go, ticker=ticker, market=market, date=date, stage=stage)


TOOLS = (
    ToolCommand("status", status_cmd, ()),
    ToolCommand("collect", collect_cmd, ()),
    ToolCommand("checkpoint", checkpoint_app, WRITE_RULES),
    ToolCommand("submit-decision", submit_cmd, (SUBMIT_RULE,)),
)
