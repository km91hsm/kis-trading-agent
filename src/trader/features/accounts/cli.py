"""`trader accounts` — 계좌 모드·일시정지 조회/변경과 KIS 연결 점검(`ping`·모의 `order-test`)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import typer

from trader.features.accounts import accounts_io, modes, secrets_io
from trader.features.accounts import order_test as order_test_mod
from trader.features.accounts.brokers import build_brokers, build_market
from trader.features.accounts.settings import load_accounts, split_accounts, to_broker_account
from trader.kernel.broker.ping import failures, run_checks
from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.config import ConfigError
from trader.kernel.config.time import KST
from trader.kernel.platform import paths

accounts_app = typer.Typer(help="계좌·모드·연결 키", no_args_is_help=True)
secrets_app = typer.Typer(help="secrets/.env 관리 (값은 출력하지 않는다)", no_args_is_help=True)
accounts_app.add_typer(secrets_app, name="secrets")


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
    """KIS 시세(005930·AAPL)·휴장일·계좌별 현금(파생 계좌는 `settings.fo` 상품으로 조립한 브로커의
    잔고 줄)을 점검한다. 실패가 있으면 exit 1."""
    b = boot(settings=True, secrets=True)
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
    stock, fo = split_accounts(accounts)
    brokers = build_brokers(b.secrets, stock)
    fo_cfg = getattr(b.settings, "fo", None)  # 덕 타이핑 — accounts 는 fo 를 import 하지 않는다
    if fo and fo_cfg is not None:
        brokers += build_brokers(b.secrets, fo, fo_product=fo_cfg.product_cfg())
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


def _fail(msg: str) -> NoReturn:
    typer.echo(f"오류: {msg}", err=True)
    raise typer.Exit(code=1)


@accounts_app.command("order-test")
@config_errors
def order_test(
    account: str = typer.Option(..., "--account", help="모의 현물 계좌 id"),
    ticker: str | None = typer.Option(
        None, "--ticker", help="종목(기본 KR 005930, US AAPL — 시장은 계좌 markets 첫 항목)"
    ),
    exchange: str = typer.Option("NAS", "--exchange", help="US 거래소 코드"),
) -> None:
    """현물 주문 연결 점검(모의 전용): 1주 지정가 매수(현재가 −10%, 호가단위 정렬)를 접수하고
    즉시 취소한다. 결과 JSON 1줄 — 취소 실패도 담고 exit 0, 접수 실패는 exit 1. 실전 계좌는 거부."""
    b = boot(secrets=True)
    accounts = load_accounts(secrets=b.secrets)
    acct = next((a for a in accounts if a.id == account), None)
    if acct is None:
        _fail(f"계좌 id 를 찾을 수 없습니다: {account}")
    if acct.env != "paper":
        _fail(f"주문 점검은 모의 계좌에서만 실행합니다: {account} ({acct.env})")
    if acct.asset != "stock":
        _fail(f"현물 계좌만 점검합니다(파생은 `trader fo order-test`): {account}")
    market = acct.markets[0]
    ticker = ticker or order_test_mod.DEFAULT_TICKER[market]
    exch = exchange if market == "US" else None
    try:
        client = build_market(b.secrets, accounts)  # 시세 키 폴백은 전체 계좌 기준(ping 과 같음)
        quote = client.get_price_kr(ticker) if market == "KR" else client.get_price_us(ticker, exch)
    except Exception as e:  # noqa: BLE001 - 점검 도구는 모든 실패를 보고한다
        _fail(f"현재가 조회 실패: {type(e).__name__}: {e}")
    brokers = build_brokers(b.secrets, [acct])
    if not brokers:
        _fail(f"{acct.env} 앱키·시크릿이 없어 점검하지 못했습니다: {account}")
    out = order_test_mod.run(
        brokers[0], quote.price, account=account, market=market, ticker=ticker, exchange=exch
    )
    typer.echo(json.dumps(out, ensure_ascii=False))
    if not out["placed"]:
        raise typer.Exit(code=1)


@accounts_app.command("add")
@config_errors
def add(
    id: str = typer.Option(..., "--id", help="계좌 id (영문·숫자·'-')"),
    env: str = typer.Option(..., "--env", help="paper | real"),
    asset: str = typer.Option("stock", "--asset", help="stock | fo"),
    markets: str = typer.Option("KR", "--markets", help="시장(쉼표 구분, 예: KR,US)"),
    mode: str = typer.Option("auto", "--mode", help="auto | approve | off (실전은 항상 off)"),
) -> None:
    """config/accounts.yaml 에 계좌 한 줄을 더한다(백업 후, 계좌번호는 자리표시자)."""
    try:
        spec = accounts_io.AccountSpec(
            id=id,
            env=env,  # type: ignore[arg-type]
            asset=asset,  # type: ignore[arg-type]
            markets=[m.strip() for m in markets.split(",") if m.strip()],  # type: ignore[misc]
            mode=mode,  # type: ignore[arg-type]
        )
    except ValueError as e:
        raise ConfigError(f"계좌 값이 올바르지 않습니다: {e}") from e
    r = accounts_io.add_account(paths.config_dir() / "accounts.yaml", spec)
    typer.echo(f"추가: {r['id']} (백업 {r['backup']})")


@accounts_app.command("remove")
@config_errors
def remove(account: str = typer.Argument(..., help="계좌 id")) -> None:
    """config/accounts.yaml 에서 계좌 한 줄을 뺀다. 살아 있는 주문·승인·보유가 있으면 거절한다."""
    b = boot(conn=True)
    try:
        try:
            r = accounts_io.remove_account(paths.config_dir() / "accounts.yaml", account, b.conn)
        except ConfigError as e:  # AccountInUse 포함
            typer.echo(f"오류: {e}", err=True)
            raise typer.Exit(code=1) from e
    finally:
        b.conn.close()
    typer.echo(f"삭제: {r['id']} (백업 {r['backup']})")


@secrets_app.command("import")
@config_errors
def secrets_import(
    src: str = typer.Option(..., "--from", help="원본 .env 경로 (예: ../Stock/.env)"),
    overwrite: bool = typer.Option(False, "--overwrite", help="이미 값이 있는 키도 바꾼다"),
    keys: str | None = typer.Option(None, "--keys", help="대상 키 이름(쉼표 구분). 없으면 전부"),
) -> None:
    """다른 프로젝트 .env 의 키를 이름 매핑(KIS_APP_KEY→KIS_PAPER_APP_KEY 등)으로 옮긴다.
    기본은 비어 있는 키만 채우고, 키 이름만 출력한다."""
    r = secrets_io.import_env(
        Path(src).expanduser(),
        paths.secrets_path(),
        overwrite=overwrite,
        keys=[k.strip() for k in keys.split(",") if k.strip()] if keys else None,
    )
    typer.echo("복사: " + (", ".join(r["copied"]) or "없음"))
    typer.echo("유지(이미 값 있음, 원본과 같음): " + (", ".join(r["same"]) or "없음"))
    typer.echo(
        "유지(이미 값 있음, 원본과 다름 → --overwrite 로 교체 가능): "
        + (", ".join(r["differs"]) or "없음")
    )
    typer.echo("원본에 없음: " + (", ".join(r["unavailable"]) or "없음"))
    if r.get("backup"):
        typer.echo(f"백업: secrets/{r['backup']}")
