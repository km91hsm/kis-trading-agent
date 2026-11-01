"""`trader accounts` — 계좌 모드·일시정지 조회/변경과 KIS 연결 점검."""

from __future__ import annotations

from datetime import datetime

import typer

from trader.features.accounts import modes
from trader.features.accounts.brokers import build_brokers, build_market
from trader.features.accounts.settings import load_accounts, to_broker_account
from trader.kernel.broker.ping import failures, run_checks
from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.config.time import KST

accounts_app = typer.Typer(help="계좌·모드·연결 키", no_args_is_help=True)


def _control_boot():
    """제어 명령 공통: 부트 → 계좌 읽기 → 원장 동기화. (conn, accounts) 를 돌려준다."""
    b = boot(secrets=True, conn=True)
    try:
        accounts = load_accounts(secrets=b.secrets)
        modes.sync_accounts(b.conn, accounts)
    except BaseException:
        b.conn.close()
        raise
    return b.conn, accounts


@accounts_app.command("show")
@config_errors
def show() -> None:
    """일시정지 여부와 계좌별 실효 모드를 보여준다."""
    conn, accounts = _control_boot()
    try:
        typer.echo(f"일시정지: {'예' if modes.is_paused(conn) else '아니오'}")
        for a in accounts:
            mode, note = modes.account_mode(conn, a)
            src = modes.mode_source(conn, a.id)
            suffix = f" — {note}" if note else ""
            typer.echo(f"{a.id} ({a.env}): {mode} [{src}]{suffix}")
    finally:
        conn.close()


@accounts_app.command("mode")
@config_errors
def mode(
    account: str = typer.Argument(..., help="계좌 id"),
    mode: str = typer.Argument(..., help="auto | approve | off"),
    confirm: str | None = typer.Option(None, "--confirm", help="실전 auto 전환 확인 문자열(LIVE)"),
) -> None:
    """계좌 실행 모드를 바꾼다. 실전 auto 는 --confirm LIVE 가 있어야 하며 확인 기록을 남긴다."""
    conn, accounts = _control_boot()
    try:
        acct = next((a for a in accounts if a.id == account), None)
        if acct is None:
            typer.echo(f"오류: 계좌 id 를 찾을 수 없습니다: {account}", err=True)
            raise typer.Exit(code=1)
        try:
            modes.set_account_mode(conn, acct, mode, confirm=confirm)
        except (modes.ConfirmationRequired, ValueError) as e:
            typer.echo(f"오류: {e}", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"{account}: {mode}")
    finally:
        conn.close()


@accounts_app.command("pause")
@config_errors
def pause() -> None:
    """paused 플래그를 켠다."""
    conn, _ = _control_boot()
    try:
        modes.set_paused(conn, True)
    finally:
        conn.close()
    typer.echo("일시정지: 예")


@accounts_app.command("resume")
@config_errors
def resume() -> None:
    """paused 플래그를 끈다."""
    conn, _ = _control_boot()
    try:
        modes.set_paused(conn, False)
    finally:
        conn.close()
    typer.echo("일시정지: 아니오")


@accounts_app.command("ping")
@config_errors
def ping(
    account: str | None = typer.Option(None, "--account", help="특정 계좌 id 만 점검"),
) -> None:
    """KIS 시세(005930·AAPL)·휴장일·계좌별 현금(파생 계좌는 잔고)을 점검한다.
    실패가 있으면 exit 1."""
    b = boot(secrets=True)
    accounts = load_accounts(secrets=b.secrets)
    if account:
        accounts = [a for a in accounts if a.id == account]
        if not accounts:
            typer.echo(f"오류: 계좌 id 를 찾을 수 없습니다: {account}", err=True)
            raise typer.Exit(code=1)
    market, market_error = None, None
    try:
        market = build_market(b.secrets, accounts)
    except Exception as e:  # noqa: BLE001 - 점검 도구는 모든 실패를 보고한다
        market_error = e
    brokers = build_brokers(b.secrets, accounts)
    checks = run_checks(
        market,
        brokers,
        [to_broker_account(a) for a in accounts],
        today=datetime.now(KST).date(),
        market_error=market_error,
    )
    for c in checks:
        typer.echo(f"{'[OK]' if c.ok else '[실패]'} {c.label}: {c.detail}")
    n = failures(checks)
    typer.echo(f"실패 {n}건")
    if n:
        raise typer.Exit(code=1)
