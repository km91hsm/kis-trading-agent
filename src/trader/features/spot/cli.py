"""`trader spot` — 현물 레인 운영 명령: 종목 분석 세션(`analyze`)과 실행 키 조회·수동 해제
(`exec-key list|release`), 승인 요청 처리(`approvals list|approve|reject`).

보낸 뒤(sent_at) 주문 행 기록 전에 끊긴 실행 키는 영구 선점이라 같은 결정·계좌·방향의 주문이 다시
나가지 않는다 — 사람이 KIS 주문 유무를 확인한 뒤 사유와 함께 푼다. 해제 기록은 control
`exec_key_released:<key>`."""

from __future__ import annotations

import typer

from trader.features.spot import analyze as analyze_mod
from trader.features.spot import executor
from trader.features.spot.messages import approval_line
from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.execution import ledger as xledger
from trader.kernel.execution.idem import ExecKeyStore
from trader.kernel.ledger import control
from trader.kernel.ledger.db import now_iso

MIN_NOTE = 10
_COLS = ("key", "created_at", "order_id", "approval_id", "sent_at")

spot_app = typer.Typer(help="현물 레인", no_args_is_help=True)
exec_key_app = typer.Typer(help="실행 키(멱등 선점) 조회·수동 해제", no_args_is_help=True)
spot_app.add_typer(exec_key_app, name="exec-key")
approvals_app = typer.Typer(help="승인 요청 조회·승인·거부", no_args_is_help=True)
spot_app.add_typer(approvals_app, name="approvals")


def _cell(v: object) -> str:
    return "-" if v is None else str(v)


@exec_key_app.command("list")
@config_errors
def exec_key_list() -> None:
    """남아 있는 실행 키(오래된 순). sent_at 이 있고 order_id 가 없으면 보낸 뒤 기록 전에
    끊긴 키다."""
    b = boot(conn=True)
    try:
        rows = ExecKeyStore(b.conn).list_all()
    finally:
        b.conn.close()
    if not rows:
        typer.echo("실행 키 없음")
        return
    typer.echo("\t".join(_COLS))
    for r in rows:
        typer.echo("\t".join(_cell(r.get(c)) for c in _COLS))


@exec_key_app.command("release")
@config_errors
def exec_key_release(
    key: str = typer.Argument(..., help="풀 실행 키(`trader spot exec-key list` 의 key)"),
    note: str = typer.Option("", "--note", help="해제 사유(10자 이상, KIS 주문 유무 확인 메모)"),
) -> None:
    """실행 키를 지운다(매수 예약도 함께). 사유 10자 이상 필수, control 에 해제 기록을 남긴다."""
    text = note.strip()
    if len(text) < MIN_NOTE:
        typer.echo(
            f"오류: 사유는 {MIN_NOTE}자 이상이어야 합니다 (--note, KIS 주문 유무 확인)", err=True
        )
        raise typer.Exit(code=1)
    b = boot(conn=True)
    try:
        store = ExecKeyStore(b.conn)
        if not any(r["key"] == key for r in store.list_all()):
            typer.echo(f"오류: 실행 키 없음: {key}", err=True)
            raise typer.Exit(code=1)
        # 감사 기록을 먼저 남긴다 — 해제 뒤 실패해도 누가 왜 풀었는지는 남아야 한다
        control.set_flag(b.conn, f"exec_key_released:{key}", {"note": text, "at": now_iso()})
        store.release(key)
    finally:
        b.conn.close()
    typer.echo(f"실행 키 해제: {key}")


@spot_app.command("analyze")
@config_errors
def analyze(
    ticker: str = typer.Option(..., "--ticker", help="종목코드/심볼"),
    market: str = typer.Option("KR", "--market", help="KR 또는 US"),
    exchange: str | None = typer.Option(None, "--exchange", help="미국 거래소 코드 (NAS/NYS/AMS)"),
    asof: str | None = typer.Option(None, "--asof", help="기준 시각 ISO8601 (기본: 현재 UTC)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="판정·기록까지만, 주문·승인 요청 없음"),
    skip_collect: bool = typer.Option(False, "--skip-collect", help="00_inputs 수집 생략"),
) -> None:
    """오케스트레이터 세션을 띄워 종목 하나를 분석하고 30_decision.json 을 만든다."""
    analyze_mod.validate_args(market, exchange)  # 부트·디렉터리 생성 전에 거른다
    b = boot(ports=True)
    try:
        code = analyze_mod.run_analyze(
            b,
            ticker=ticker,
            market=market,
            exchange=exchange,
            asof=asof,
            dry_run=dry_run,
            skip_collect=skip_collect,
        )
    finally:
        b.conn.close()
    if code:
        raise typer.Exit(code=code)


@approvals_app.command("list")
@config_errors
def approvals_list() -> None:
    """만료분을 정리한 뒤 승인 대기 요청을 한 줄씩 보여 준다."""
    b = boot(ports=True)
    try:
        ectx = executor.exec_ctx(b)
        executor.expire_approvals(ectx)
        envs = {a.id: a.env for a in ectx.accounts}
        pending = xledger.pending_approvals(b.conn)
    finally:
        b.conn.close()
    if not pending:
        typer.echo("승인 대기 없음")
        return
    for a in pending:
        typer.echo(approval_line(a, envs.get(a["account_id"])))


@approvals_app.command("approve")
@config_errors
def approvals_approve(approval_id: int = typer.Argument(..., help="승인 id")) -> None:
    """승인 요청을 주문으로 실행한다."""
    b = boot(ports=True)
    try:
        r = executor.approve(executor.exec_ctx(b), approval_id)
    finally:
        b.conn.close()
    if not r["ok"]:
        order = r.get("order") or {}
        err = r.get("error") or order.get("message") or r.get("reason") or r.get("status")
        typer.echo(f"승인 실패 #{approval_id}: {err}", err=True)
        raise typer.Exit(code=1)
    order = r.get("order") or {}
    typer.echo(f"승인 실행 #{approval_id}: 주문번호 {order.get('order_no') or '-'}")


@approvals_app.command("reject")
@config_errors
def approvals_reject(approval_id: int = typer.Argument(..., help="승인 id")) -> None:
    """승인 요청을 거부한다."""
    b = boot(ports=True)
    try:
        r = executor.reject(executor.exec_ctx(b), approval_id)
    finally:
        b.conn.close()
    if not r["ok"]:
        typer.echo(f"거부 실패 #{approval_id}: {r.get('error') or r.get('status')}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"거부 #{approval_id}")
