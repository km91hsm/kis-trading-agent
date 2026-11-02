"""`trader spot` — 현물 레인 운영 명령. 지금은 실행 키 조회·수동 해제(`exec-key list|release`)만.

보낸 뒤(sent_at) 주문 행 기록 전에 끊긴 실행 키는 영구 선점이라 같은 결정·계좌·방향의 주문이 다시
나가지 않는다 — 사람이 KIS 주문 유무를 확인한 뒤 사유와 함께 푼다. 해제 기록은 control
`exec_key_released:<key>`."""

from __future__ import annotations

import typer

from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.execution.idem import ExecKeyStore
from trader.kernel.ledger import control
from trader.kernel.ledger.db import now_iso

MIN_NOTE = 10
_COLS = ("key", "created_at", "order_id", "approval_id", "sent_at")

spot_app = typer.Typer(help="현물 레인", no_args_is_help=True)
exec_key_app = typer.Typer(help="실행 키(멱등 선점) 조회·수동 해제", no_args_is_help=True)
spot_app.add_typer(exec_key_app, name="exec-key")


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
        store.release(key)
        control.set_flag(b.conn, f"exec_key_released:{key}", {"note": text, "at": now_iso()})
    finally:
        b.conn.close()
    typer.echo(f"실행 키 해제: {key}")
