"""`trader regime` — 시장 국면 조회(`show`)·과거 재현(`backtest`)·
킬스위치(`killswitch on|off|show`).

킬스위치 해제(`off`)는 사람만, 원인 메모 `killswitch.MIN_NOTE` 자 이상이 필요하고 기준 NAV 를
다시 잡는다."""

from __future__ import annotations

import json
from typing import Any

import typer

from trader.features.regime import killswitch
from trader.features.regime import regime as regime_mod
from trader.kernel.cli.boot import Boot, boot, config_errors
from trader.kernel.config import ConfigError

regime_app = typer.Typer(
    help="시장 국면 (Phase 17 H): 조회·과거 재현·킬스위치", no_args_is_help=True
)
killswitch_app = typer.Typer(help="누적 손실 킬스위치(신규 매수 중단)", no_args_is_help=True)
regime_app.add_typer(killswitch_app, name="killswitch")


def _market_client(b: Boot) -> Any:
    """시세 클라이언트(계좌 포트). 포트·키가 없으면 ConfigError."""
    port = b.ports.get("accounts")
    mc = port.market_client() if port is not None else None
    if mc is None:
        raise ConfigError("시세 클라이언트를 만들 수 없어요 (계좌·시세 키 확인)")
    return mc


@regime_app.command("show")
@config_errors
def regime_show() -> None:
    """시장별 현재 국면(control 플래그)과 헤지 설정을 JSON 으로."""
    b = boot(settings=True, conn=True)
    try:
        out = {
            "hedge": b.settings.hedge.model_dump(),
            "markets": regime_mod.summary(b.conn, list(b.settings.markets)),
        }
    finally:
        b.conn.close()
    typer.echo(json.dumps(out, ensure_ascii=False, default=str))


@regime_app.command("backtest")
@config_errors
def regime_backtest(
    market: str = typer.Option("KR", "--market", help="KR|US"),
    days: int = typer.Option(1000, "--days", min=260, max=1000, help="지수 proxy 일봉 수"),
) -> None:
    """지수 proxy 일봉으로 과거 bear 구간을 재현한다 (임계값 확정용, 상태는 바꾸지 않음)."""
    if market not in ("KR", "US"):
        raise ConfigError(f"시장은 KR 또는 US: {market}")
    b = boot(ports=True)
    try:
        settings = b.settings
        inst = settings.hedge.index_proxy.get(market)
        if inst is None:
            raise ConfigError(f"{market} index_proxy 미설정")
        mc = _market_client(b)
        candles = (
            mc.get_daily_us(inst.symbol, inst.exchange or "AMS", days=days)
            if market == "US"
            else mc.get_daily_kr(inst.symbol, days=days)
        )
    finally:
        b.conn.close()
    rows = [(c.date, c.close) for c in candles]
    spans = regime_mod.backtest(rows, settings.hedge)
    out = {
        "market": market,
        "proxy": inst.symbol,
        "candles": len(rows),
        "range": [rows[0][0].isoformat(), rows[-1][0].isoformat()] if rows else None,
        "bear_spans": spans,
        "transitions": len(spans),
    }
    typer.echo(json.dumps(out, ensure_ascii=False, default=str))


def _show(conn) -> None:
    r = killswitch.reason(conn)
    typer.echo(f"킬스위치: {'켜짐 — ' + r if r else '꺼짐'}")


@killswitch_app.command("on")
@config_errors
def killswitch_on(reason: str = typer.Option("", "--reason", help="켜는 사유")) -> None:
    """수동으로 켠다(전 계좌 신규 매수 중단, 매도·손절·헤지는 계속)."""
    b = boot(conn=True)
    try:
        killswitch.engage(b.conn, reason=reason.strip() or "수동(CLI)", source="cli")
        _show(b.conn)
    finally:
        b.conn.close()


@killswitch_app.command("off")
@config_errors
def killswitch_off(
    note: str = typer.Option(
        "", "--note", help=f"원인 메모({killswitch.MIN_NOTE}자 이상, 기준 NAV 재설정)"
    ),
) -> None:
    """해제한다(사람만). 원인 메모가 짧으면 거부, 모든 계좌·시장 기준을 최근 NAV 로 다시 잡는다."""
    if len(note.strip()) < killswitch.MIN_NOTE:
        typer.echo(f"오류: 해제 메모는 {killswitch.MIN_NOTE}자 이상 원인을 적어야 합니다", err=True)
        raise typer.Exit(code=1)
    b = boot(conn=True)
    try:
        killswitch.release(b.conn, note=note)
        _show(b.conn)
    finally:
        b.conn.close()


@killswitch_app.command("show")
@config_errors
def killswitch_show() -> None:
    """현재 상태(켜짐이면 사유)."""
    b = boot(conn=True)
    try:
        _show(b.conn)
    finally:
        b.conn.close()
