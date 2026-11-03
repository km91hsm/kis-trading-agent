"""`trader scan` — 테마 열기 조회(`theme show`)·사건 감지 조회(`events show`)·정량 스크린 과거 검증
(`backtest`)·유니버스 점검(`universe check|sectors`). 모두 읽기 전용이고 출력은 JSON 한 덩어리다.
옛 `events check`(수혜 표 시세 확인)는 이식하지 않았다(README)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import typer

from trader.features.ingest import api as ingest_api
from trader.features.ml import api as ml_api
from trader.features.saveticker import api as saveticker_api
from trader.features.scan import backtest, events, run, sector_suggest, theme, universe_check
from trader.features.scan.settings import load_events
from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.config import ConfigError
from trader.kernel.lane.types import LaneContext
from trader.kernel.ledger import control
from trader.kernel.platform import bundle, paths
from trader.kernel.session.run_store import run_date_for
from trader.kernel.session.runner import PipelineError

scan_app = typer.Typer(help="테마 열기·사건 수혜 조회", no_args_is_help=True)
theme_app = typer.Typer(help="테마 열기 감지: 조회", no_args_is_help=True)
events_app = typer.Typer(help="사건 수혜: 뉴스 감지 결과 보기", no_args_is_help=True)
universe_app = typer.Typer(help="유니버스 점검", no_args_is_help=True)
scan_app.add_typer(theme_app, name="theme")
scan_app.add_typer(events_app, name="events")
scan_app.add_typer(universe_app, name="universe")


def _market(market: str) -> str:
    if market not in ("KR", "US"):
        raise ConfigError("--market 은 KR|US")
    return market


@theme_app.command("show")
@config_errors
def theme_show(
    market: str = typer.Option("KR", "--market", help="KR|US"),
    live: bool = typer.Option(
        False, "--live", help="저장된 결과 대신 지금 계산(KIS 순위 조회, 저장 안 함)"
    ),
) -> None:
    """오늘 테마 열기 결과(데몬 <시장>_theme 이 남긴 control 값)를 JSON 으로. 상태 불변."""
    _market(market)
    b = boot(settings=True, conn=True, ports=live)
    try:
        if live:
            port = b.ports.get("accounts")
            try:
                mc = port.market_client() if port is not None else None
            except Exception:  # noqa: BLE001 - 키가 없으면 순위 없이
                mc = None
            now = datetime.now(UTC)
            root = saveticker_api.resolve_dir(saveticker_api.load_cfg(registry=b.registry).dir)
            out = theme.compute(
                b.conn,
                b.settings,
                market=market,
                today=run_date_for(market, now),
                now=now,
                market_client=mc,
                saveticker_root=root,
            )
        else:
            out = control.get_flag(b.conn, f"{theme.FLAG}:{market}") or {
                "market": market,
                "themes": [],
                "note": "아직 결과 없음 — 데몬 장중 잡(<시장>_theme) 또는 --live",
            }
    finally:
        b.conn.close()
    typer.echo(json.dumps(out, ensure_ascii=False, default=str))


@events_app.command("show")
@config_errors
def events_show(market: str = typer.Option("KR", "--market", help="KR|US")) -> None:
    """지금 뉴스 저장소로 사건을 감지해 JSON 으로(기록·알림 없음, 읽기만)."""
    _market(market)
    b = boot(conn=True)
    try:
        cfg = load_events(b.registry)
        found = events.detect(b.conn, cfg, market=market, asof=datetime.now(UTC))
    finally:
        b.conn.close()
    typer.echo(
        json.dumps(
            {"enabled": cfg.enabled, "market": market, "detected": found},
            ensure_ascii=False,
        )
    )


@scan_app.command("run")
@config_errors
def scan_run(
    market: str = typer.Option("KR", "--market", help="KR 또는 US"),
    asof: str | None = typer.Option(None, "--asof", help="기준 시각 ISO8601 (기본: 현재 UTC)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="screener 세션 없이 정량 상위만"),
) -> None:
    """정량 필터 → screener 세션 → 오늘 분석 대상 목록 (_scan.json, stdout JSON)."""
    if market not in ("KR", "US"):
        raise ConfigError(f"시장은 KR 또는 US 여야 합니다: {market}")
    try:
        when = datetime.fromisoformat(asof) if asof else datetime.now(UTC)
    except ValueError as e:
        raise ConfigError(f"--asof 는 ISO8601 이어야 합니다: {asof}") from e
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    b = boot(ports=True)
    try:
        ctx = LaneContext(
            b.settings,
            b.conn,
            state=paths.state_dir(),
            now=lambda: when,
            dry_run=dry_run,
            notifier=None,
            ports=b.ports,
            ext={"registry": b.registry},
        )
        out = run.run_scan(ctx, market=market, dry_run=dry_run)
    except (PipelineError, ValueError) as e:
        typer.echo(f"오류: {e}", err=True)
        raise typer.Exit(code=1) from e
    finally:
        b.conn.close()
    typer.echo(json.dumps(out, ensure_ascii=False, default=str))


@scan_app.command("backtest")
@config_errors
def scan_backtest(
    market: str = typer.Option("KR", "--market", help="KR|US"),
    days: int = typer.Option(1100, "--days", min=30, help="최근 N일 피처 파일"),
    top: int = typer.Option(5, "--top", min=1, max=30, help="바구니 종목 수"),
    horizon: int = typer.Option(5, "--horizon", min=1, max=60, help="보유 거래일"),
) -> None:
    """`roc_10` 상위 N·실제 scan 점수 상위 N·필터 통과 1/N 을 다음 거래일 진입, horizon 거래일
    보유, 같은 왕복비용으로 비교한다(겹치지 않는 창). 읽기 전용 — 설정·주문 불변."""
    if market not in ("KR", "US"):
        raise ConfigError(f"시장은 KR 또는 US: {market}")
    b = boot(conn=True)
    try:
        since = date.today() - timedelta(days=days)
        df = ml_api.load_all(
            paths.state_dir(),
            market=market,
            since=since,
            columns=["close", "volume", "roc_10", "high_52w_pos", "atr_14"],
        )
        # 시점 유니버스는 신호일 선택에만 — 가격표는 걸러지지 않은 df(PR #62 Codex)
        eligible, pit = ml_api.pit_apply(df, b.conn, ml_api.load_ml(b.registry).train.pit)
    finally:
        b.conn.close()
    out = backtest.run(
        df,
        market=market,
        top_n=top,
        horizon=horizon,
        cost_bp=backtest.ROUND_TRIP_BP[market],
        select=eligible,
    )
    out["pit"] = pit
    out["note"] = (
        "survivorship: 피처 스토어는 오늘 유니버스로 백필 — pit.recorded_since 이전 구간은 "
        "생존 편향이 남는다(편출·상장폐지 종목 없음)"
    )
    typer.echo(json.dumps(out, ensure_ascii=False, default=str))


def _market_client(boot_fn: Callable[..., Any]) -> Any:
    """KIS 시세 클라이언트(`ports["accounts"].market_client()`). 테스트가 갈아끼우는 지점."""
    return boot_fn(ports=True).ports["accounts"].market_client()


@universe_app.command("check")
@config_errors
def universe_check_cmd(
    market: str = typer.Option("KR", "--market", help="KR 또는 US"),
    show_all: bool = typer.Option(False, "--all", help="문제 없는 종목도 출력"),
) -> None:
    """유니버스 종목이 KIS 시세를 실제로 주는지 점검한다 (US 는 거래소 코드 제안). 문제면 exit 1."""
    if market not in ("KR", "US"):
        raise ConfigError(f"시장은 KR 또는 US 여야 합니다: {market}")
    universe = ingest_api.load_universe()
    rows = universe_check.check(universe, _market_client(boot), market=market)
    bad = [r for r in rows if not r["ok"]]
    for r in rows if show_all else bad:
        mark = "OK " if r["ok"] else "NG "
        ex = f" {r['exchange']}" if r["exchange"] else ""
        hint = f" → {r['suggested_exchange']}" if r["suggested_exchange"] else ""
        typer.echo(f"{mark}{r['ticker']}{ex}{hint} {r['note']}".rstrip())
    typer.echo(f"점검 {len(rows)}종목, 문제 {len(bad)}건")
    if bad:
        raise typer.Exit(code=1)


def _templates_config_dir() -> Path:
    """config/*.yaml 의 사본이 있는 templates/config (개발 체크아웃).

    번들(_internal)에는 쓰지 않는다."""
    return paths.project_root() / "templates" / "config"


@universe_app.command("sectors")
@config_errors
def universe_sectors_cmd(
    market: str = typer.Option("KR", "--market", help="KR 또는 US"),
    show_all: bool = typer.Option(False, "--all", help="이미 섹터가 있는 종목도 출력"),
    write: bool = typer.Option(
        False, "--write", help="제안을 config/sectors.yaml(+템플릿)에 덧붙인다"
    ),
) -> None:
    """KIS 업종으로 sectors.yaml 에 빠진 종목의 섹터를 제안한다. 표에 없는 업종이 있으면 exit 1."""
    if market not in ("KR", "US"):
        raise ConfigError(f"시장은 KR 또는 US 여야 합니다: {market}")
    universe = ingest_api.load_universe()
    sectors = ingest_api.load_sectors()
    rows = sector_suggest.suggest(
        universe, sectors, _market_client(boot), market=market, only_missing=not show_all
    )
    for r in rows:
        ex = f" {r['exchange']}" if r["exchange"] else ""
        typer.echo(
            f"{r['ticker']}{ex}  업종={r['industry'] or '-'}  현재={r['current'] or '-'}  "
            f"제안={r['suggested'] or '-'}  {r['note']}".rstrip()
        )
    unresolved = [r for r in rows if r["current"] is None and not r["suggested"]]
    if write:
        today = date.today()
        n = sector_suggest.apply(paths.config_dir() / "sectors.yaml", market, rows, today=today)
        tmpl = _templates_config_dir() / "sectors.yaml"
        wrote_tmpl = False
        if not bundle.is_frozen() and tmpl.is_file():
            # 템플릿은 KIS 제안이 아니라 config 값으로 맞춘다 (config 에만 있던 수동 항목도 함께)
            sector_suggest.sync(paths.config_dir() / "sectors.yaml", tmpl, market, today=today)
            wrote_tmpl = True
        typer.echo(f"기록: {n}종목 (config/sectors.yaml{'·templates' if wrote_tmpl else ''})")
    typer.echo(f"점검 {len(rows)}종목, 제안 없음 {len(unresolved)}건")
    if unresolved:
        raise typer.Exit(code=1)
