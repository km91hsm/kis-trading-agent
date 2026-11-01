"""`trader ingest` 명령. 지금은 `collect`(종목 하나의 00_inputs.json 수집·저장)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer

from trader.features.ingest import clients, inputs
from trader.features.ingest.settings import load_sources
from trader.features.saveticker.api import load_cfg as load_saveticker_cfg
from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.config import ConfigError
from trader.kernel.session.run_store import read_checkpoint, run_date_for, run_dir

ingest_app = typer.Typer(name="ingest", help="수집", no_args_is_help=True)


@ingest_app.command("collect")
@config_errors
def collect(
    ticker: str = typer.Option(..., "--ticker", help="종목코드/심볼"),
    market: str = typer.Option("KR", "--market", help="KR 또는 US"),
    exchange: str | None = typer.Option(None, "--exchange", help="미국 거래소 코드 (NAS/NYS/AMS)"),
    asof: str | None = typer.Option(
        None, "--asof", help="기준 시각 ISO8601 (기본: 현재 UTC, 24시간 넘은 과거는 거부)"
    ),
    force: bool = typer.Option(False, "--force", help="기존 00_inputs 를 덮어씀"),
) -> None:
    """종목의 00_inputs.json 을 수집·저장한다. 일부 절 실패는 정상(exit 0)."""
    try:
        if market not in ("KR", "US"):
            raise ConfigError(f"시장은 KR 또는 US 여야 합니다: {market}")
        b = boot(secrets=True, log_name="ingest")
        cfg = load_sources(registry=b.registry)
        when = datetime.fromisoformat(asof) if asof else datetime.now(UTC)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        # 시세 클라이언트는 계좌 소유 — M3 에서는 없이(시세 절 건너뜀), M4 가 주입한다
        deps = clients.build_input_deps(b.secrets, cfg)
        existed = (
            not force
            and read_checkpoint(run_dir(run_date_for(market, when), market, ticker), "00_inputs")  # type: ignore[arg-type]
            is not None
        )
        path = inputs.collect_and_save(
            ticker=ticker,
            market=market,  # type: ignore[arg-type]
            exchange=exchange,
            asof=when,
            cfg=cfg,
            secrets=b.secrets,
            deps=deps,
            force=force,
            saveticker_cfg=load_saveticker_cfg(registry=b.registry),
        )
        errors = json.loads(path.read_text(encoding="utf-8")).get("errors", [])
    except ValueError as e:  # ConfigError 는 config_errors 가 처리한다
        typer.echo(f"오류: {e}", err=True)
        raise typer.Exit(code=1) from e
    if existed:
        typer.echo(f"건너뜀(이미 존재, --force 로 덮어씀): {path}")
    else:
        typer.echo(f"저장: {path}")
    for e in errors:
        typer.echo(f"  - {e}")
    typer.echo(f"오류 절 {len(errors)}건")
