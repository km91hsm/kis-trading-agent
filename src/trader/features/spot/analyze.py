"""`trader spot analyze` 본문: 입력 수집 → 오케스트레이터 세션 → 결정 출력.

종목 하나를 분석해 `30_decision.json` 을 만든다. 이미 있는 단계는 건너뛰므로 재실행하면 빠진
단계부터 이어간다. `FAKE_CLAUDE=1` 이면 claude 를 띄우지 않고 파이썬 흉내(`fake.py`)가 돈다."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import typer

from trader.features.spot.executor import boot_notifier
from trader.features.spot.schemas import CHECKPOINT_NAMES
from trader.features.spot.session_kinds import AGENTS, ANALYZE
from trader.kernel.cli.boot import Boot
from trader.kernel.config import ConfigError
from trader.kernel.lane.types import LaneContext
from trader.kernel.platform import paths
from trader.kernel.platform.claude_bin import ClaudeNotFound
from trader.kernel.session.agents import by_name
from trader.kernel.session.run_store import read_checkpoint, run_date_for, run_dir
from trader.kernel.session.runner import PipelineError, SessionSpec, run_session

logger = logging.getLogger("trader.spot")


def run_analyze(
    boot: Boot,
    *,
    ticker: str,
    market: str,
    exchange: str | None,
    asof: str | None,
    dry_run: bool,
    skip_collect: bool,
) -> int:
    """종료 코드(0 성공·1 실패)를 돌려준다. 연결은 호출자가 닫는다."""
    try:
        return _run(
            boot,
            ticker=ticker,
            market=market,
            exchange=exchange,
            asof=asof,
            dry_run=dry_run,
            skip_collect=skip_collect,
        )
    except (ConfigError, PipelineError, ClaudeNotFound, ValueError) as e:
        typer.echo(f"오류: {e}", err=True)
        return 1


def validate_args(market: str, exchange: str | None) -> None:
    """디렉터리·부트 전에 부르는 인자 검사."""
    if market not in ("KR", "US"):
        raise ConfigError(f"시장은 KR 또는 US 여야 합니다: {market}")
    if market == "US" and not exchange:
        raise ConfigError("미국 종목은 --exchange(거래소 코드)가 필요합니다")


def _run(
    boot: Boot,
    *,
    ticker: str,
    market: str,
    exchange: str | None,
    asof: str | None,
    dry_run: bool,
    skip_collect: bool,
) -> int:
    validate_args(market, exchange)
    when = datetime.fromisoformat(asof) if asof else datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    day = run_date_for(market, when)  # US 는 뉴욕 운영일 (데몬·monitor 와 같은 디렉터리)
    state = paths.state_dir()
    d = run_dir(day, market, ticker, state)
    if read_checkpoint(d, "00_inputs") is None:
        if skip_collect:
            raise ConfigError(f"00_inputs 가 없습니다: {d} (--skip-collect 해제)")
        acc = boot.ports.get("accounts")
        boot.ports["inputs"].collect(
            ticker=ticker,
            market=market,
            exchange=exchange,
            asof=when,
            force=False,
            conn_factory=None,
            market_client=acc.market_client() if acc is not None else None,
            state=state,
        )
    ctx = LaneContext(
        settings=boot.settings,
        conn=boot.conn,
        state=state,
        now=lambda: datetime.now(UTC),
        dry_run=dry_run,
        notifier=boot_notifier(boot),
        ports=boot.ports,
    )
    res = run_session(
        SessionSpec(
            market=market,
            date=day,
            tickers=[{"ticker": ticker, "exchange": exchange}],
            dry_run=dry_run,
            asof=when,
        ),
        ANALYZE,
        ctx,
        agents=by_name(AGENTS),
        checkpoint_names=CHECKPOINT_NAMES,
    )
    if not res.ok:
        typer.echo(f"세션 실패: {res.error}", err=True)
        return 1
    mode = "FAKE" if res.fake else f"session={res.session_id}"
    typer.echo(f"세션 완료 ({mode}) 비용 ${res.cost_usd or 0:.2f}")
    if res.stages_written:
        typer.echo("새로 쓴 단계: " + ", ".join(res.stages_written))
    decision = read_checkpoint(d, "30_decision")
    if decision is None:
        typer.echo("결정 없음 (30_decision.json 이 만들어지지 않았습니다)", err=True)
        return 1
    data = decision["data"]
    typer.echo(
        f"결정: {data['action']} target_weight={data['target_weight']} "
        f"confidence={data['confidence']}"
    )
    typer.echo(f"저장: {d / '30_decision.json'}")
    errors = (read_checkpoint(d, "00_inputs") or {}).get("errors", [])
    if errors:
        typer.echo(f"입력 오류 절 {len(errors)}건: {json.dumps(errors, ensure_ascii=False)[:300]}")
    return 0
