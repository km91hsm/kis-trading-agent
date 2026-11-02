"""spot 텔레그램 명령 `/approve <id> [실전]`·`/reject <id>`.

핸들러는 답장 문자열을 돌려주고 보내는 일은 디스패처가 한다. ExecContext 는 `ctx.ports["accounts"]`
로 조립한다 — 포트가 없으면 fail-closed(안내만, 주문 없음). 실전 계좌 승인은 확인어(`실전`)가 없으면
실행하지 않는다."""

from __future__ import annotations

from trader.features.spot import executor
from trader.features.spot.messages import approval_line
from trader.kernel.execution import ledger as xledger
from trader.kernel.notify import messages
from trader.kernel.notify.commands import CommandContext
from trader.kernel.registry.types import TelegramCommand

NO_PORT = "계좌 포트가 없어 처리할 수 없어요 (주문 없음)"


def _exec_ctx(ctx: CommandContext) -> executor.ExecContext | None:
    port = ctx.ports.get("accounts")
    if port is None:
        return None
    return executor.exec_ctx_from(ctx.settings, ctx.conn, port, ctx.notifier, ctx.now, ctx.state)


def _parse(arg: str | None) -> tuple[int, list[str]] | None:
    parts = (arg or "").split()
    if not parts or not parts[0].isdigit():
        return None
    return int(parts[0]), parts[1:]


def handle_approve(ctx: CommandContext, arg: str | None) -> str:
    parsed = _parse(arg)
    if parsed is None:
        return "사용법: /approve <승인 id>"
    aid, extra = parsed
    ectx = _exec_ctx(ctx)
    if ectx is None:
        return messages.event(f"승인 실패 #{aid}", NO_PORT)
    pa = xledger.get_approval(ctx.conn, aid)
    acct = next((x for x in ectx.accounts if pa and x.id == pa["account_id"]), None)
    if acct is not None and acct.env != "paper" and messages.REAL_CONFIRM not in extra:
        # 실전 계좌 승인은 확인어를 붙여야 한다 — 웹의 확인창과 같은 한 번 더 묻기
        return messages.event(
            f"확인 필요 #{aid}",
            f"{acct.id} 은(는) 실전 계좌예요 — 실제 돈으로 주문해요.",
            f"진행하려면: /approve {aid} {messages.REAL_CONFIRM}",
        )
    r = executor.approve(ectx, aid)
    order = r.get("order") or {}
    if r["ok"]:
        env = acct.env if acct is not None else None
        line = approval_line(pa, env) if pa else f"#{aid}"
        return messages.event(
            f"승인 실행 #{aid}", line, f"주문번호: {order.get('order_no') or '-'}"
        )
    if order.get("status") == "unknown":
        # 응답 없음 — executor 가 이미 [주문 상태 미확인] 을 보냈다(실패로 덧알리면 재주문 유도)
        return f"#{aid} 주문 상태 미확인 — 앞선 알림을 확인하세요"
    err = r.get("error") or order.get("message") or r.get("reason") or "알 수 없음"
    return messages.failure(f"승인 실패 #{aid}", "", err)


def handle_reject(ctx: CommandContext, arg: str | None) -> str:
    parsed = _parse(arg)
    if parsed is None:
        return "사용법: /reject <승인 id>"
    aid, _ = parsed
    ectx = _exec_ctx(ctx)
    if ectx is None:
        return messages.event(f"거부 실패 #{aid}", NO_PORT)
    r = executor.reject(ectx, aid)
    if r["ok"]:
        return f"#{aid} 거부했어요"
    return messages.failure(f"거부 실패 #{aid}", "", r.get("error") or "알 수 없음")


TELEGRAM_COMMANDS = (
    TelegramCommand("approve", False, "/approve <id> [실전] — 승인 요청 실행", handle_approve),
    TelegramCommand("reject", False, "/reject <id> — 승인 요청 거부", handle_reject),
)
