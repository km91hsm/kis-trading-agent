"""`trader scan` — 테마 열기 조회(`theme show`)·사건 감지 조회(`events show`). 둘 다 읽기 전용이고
출력은 JSON 한 덩어리다. 옛 `events check`(수혜 표 시세 확인)는 이식하지 않았다(README)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer

from trader.features.saveticker import api as saveticker_api
from trader.features.scan import events, theme
from trader.features.scan.settings import load_events
from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.config import ConfigError
from trader.kernel.ledger import control
from trader.kernel.session.run_store import run_date_for

scan_app = typer.Typer(help="테마 열기·사건 수혜 조회", no_args_is_help=True)
theme_app = typer.Typer(help="테마 열기 감지: 조회", no_args_is_help=True)
events_app = typer.Typer(help="사건 수혜: 뉴스 감지 결과 보기", no_args_is_help=True)
scan_app.add_typer(theme_app, name="theme")
scan_app.add_typer(events_app, name="events")


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
