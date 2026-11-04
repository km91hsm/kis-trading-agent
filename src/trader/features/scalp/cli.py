"""`trader scalp …` — 1분봉 백테스트·실적 보고 (옛 `trader scalp backtest|report` 이식, 읽기 전용).

레인이라 다른 기능을 import 하지 않는다 — 시세 클라이언트는 `accounts` 포트에서 받는다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import typer

from trader.features.scalp import engine, signals
from trader.kernel.cli.boot import Boot, boot, config_errors
from trader.kernel.config import ConfigError
from trader.kernel.config.loader import memo_load
from trader.kernel.config.market_hours import REGULAR
from trader.kernel.execution import ledger as xledger
from trader.kernel.platform import paths
from trader.kernel.session.run_store import run_date_for

MINUTE_BARS = 400  # KIS 당일 분봉 최대 요청 수

scalp_app = typer.Typer(
    help="분 단위 단타(모의 전용·기본 끔): 1분봉 백테스트·실적 보고", no_args_is_help=True
)


def _check_market(market: str) -> None:
    if market not in ("KR", "US"):
        raise ConfigError(f"시장은 KR 또는 US 여야 합니다: {market}")


def _market_client(b: Boot) -> Any:
    """시세 클라이언트(계좌 포트). 포트·키가 없으면 ConfigError."""
    port = b.ports.get("accounts")
    mc = port.market_client() if port is not None else None
    if mc is None:
        raise ConfigError("시세 클라이언트를 만들 수 없어요 (계좌·시세 키 확인)")
    return mc


def _monitor(b: Boot, market: str) -> tuple[str, str]:
    """장중 감시 창 (start, end) — `<시장>.monitor`(Ruling A4). schedule.yaml 이 깨졌으면 정규장."""
    try:
        ms = getattr(memo_load("schedule", b.registry), market.lower()).monitor
        return ms.start, ms.end
    except ConfigError:
        return REGULAR[market]


@scalp_app.command("backtest")
@config_errors
def backtest(
    ticker: str = typer.Option(..., "--ticker", help="종목 코드"),
    market: str = typer.Option("KR", "--market", help="KR|US"),
    exchange: str | None = typer.Option(None, "--exchange", help="US 거래소 NAS|NYS|AMS"),
    days: int = typer.Option(1, "--days", min=1, max=5, help="최근 N일 (KIS 당일 분봉만 — 오늘)"),
) -> None:
    """현재 분봉(KIS 당일, 실전 시세 키 필요)으로 단타 규칙을 재현해 JSON 으로. 설정 불변·주문 없음.
    KIS 분봉은 당일분만 주므로 --days 는 상태 파일이 있는 과거 날의 기록을 더해 보여 준다."""
    _check_market(market)
    b = boot(ports=True)
    try:
        cfg = b.settings.scalp
        open_hm, close_hm = _monitor(b, market)
        mc = _market_client(b)
        bars = (
            mc.get_minute_kr(ticker, count=MINUTE_BARS)
            if market == "KR"
            else mc.get_minute_us(ticker, exchange or "NAS", count=MINUTE_BARS)
        )
    finally:
        b.conn.close()
    out = {
        "ticker": ticker,
        "market": market,
        "bars": len(bars),
        "today": signals.backtest_day(bars, cfg, open_hm=open_hm, close_hm=close_hm),
        "config": cfg.model_dump(),
        "report": engine.report(
            paths.state_dir(),
            market=market,
            days=days,
            today=run_date_for(market, datetime.now(UTC)),
        ),
    }
    typer.echo(json.dumps(out, ensure_ascii=False, default=str))


@scalp_app.command("report")
@config_errors
def report(
    market: str = typer.Option("KR", "--market", help="KR|US"),
    days: int = typer.Option(5, "--days", min=1, max=60, help="최근 운영일 수"),
    live: bool = typer.Option(
        False, "--live", help="오늘 NO_GO 판정의 가상 수익을 당일 분봉으로 계산(실전 시세 키)"
    ),
) -> None:
    """단타 상태 파일(state/scalp/<date>/<market>.json)로 승률·사유별 집계 + 판정자 GO vs NO_GO
    사후 비교를 JSON 으로."""
    _check_market(market)
    b = boot(ports=live, settings=True, conn=True)
    try:
        cfg = b.settings.scalp
        _open_hm, close_hm = _monitor(b, market)
        judge: dict[str, Any] = {"cfg": cfg, "close_hm": close_hm, "minutes_fn": None}
        if live:  # 오늘 NO_GO 신호의 가상 수익 — KIS 당일 분봉(실전 시세 키)
            mc = _market_client(b)
            conn = b.conn

            def us_minutes(t: str, ex: str | None = None) -> list:
                ex = ex or xledger.exchange_of(conn, "US", t) or "NAS"
                return mc.get_minute_us(t, ex, count=MINUTE_BARS)

            judge["minutes_fn"] = (
                (lambda t, ex=None: mc.get_minute_kr(t, count=MINUTE_BARS))
                if market == "KR"
                else us_minutes
            )
        out = engine.report(
            paths.state_dir(),
            market=market,
            days=days,
            today=run_date_for(market, datetime.now(UTC)),
            judge=judge,
        )
    finally:
        b.conn.close()
    out["enabled"] = cfg.enabled
    out["judge_enabled"] = cfg.judge
    typer.echo(json.dumps(out, ensure_ascii=False, default=str))
