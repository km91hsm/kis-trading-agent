"""spot 텔레그램 명령 `/approve <id> [실전]`·`/reject <id>` 와 승인 버튼 콜백 `apv:*`.

핸들러는 답장 문자열을 돌려주고 보내는 일은 디스패처가 한다. ExecContext 는 `ctx.ports["accounts"]`
로 조립한다 — 포트가 없으면 fail-closed(안내만, 주문 없음). 결정은 모두 `approvals.decide`(선점·
기록)를 지나고, 텔레그램 핸들러는 데몬 안에서 돌아 결정이 되면 바로 집행한다(`approvals.execute`).

실전 계좌 승인은 확인이 필요하다: 텍스트는 확인어(`실전`), 버튼은 2단계 — `apv:<id>:ask` 가 계좌
id 를 실은 확인 버튼(`apv:<id>:yes:<계좌>`)을 새 메시지로 보내고, 그 버튼만 실전 승인을 결정한다.
같은 버튼을 두 번 눌러도 두 번째 결정은 거부된다(커널은 중복 누름을 막지 않는다)."""

from __future__ import annotations

from trader.features.spot import approvals, executor
from trader.features.spot.messages import (
    APPROVAL_PREFIX,
    approval_line,
    real_confirm_button,
    reject_notice,
)
from trader.kernel.execution import ledger as xledger
from trader.kernel.notify import messages
from trader.kernel.notify.commands import CommandContext
from trader.kernel.notify.telegram import CALLBACK_DATA_MAX, Message, send
from trader.kernel.registry.types import TelegramCallback, TelegramCommand

NO_PORT = "계좌 포트가 없어 처리할 수 없어요 (주문 없음)"
BAD_BUTTON = "알 수 없는 승인 버튼이에요 (주문 없음)"
VERBS = ("yes", "no", "ask")


def _exec_ctx(ctx: CommandContext) -> executor.ExecContext | None:
    port = ctx.ports.get("accounts")
    if port is None:
        return None
    return executor.exec_ctx_from(
        ctx.settings, ctx.conn, port, ctx.notifier, ctx.now, ctx.state, ports=ctx.ports
    )


def _parse(arg: str | None) -> tuple[int, list[str]] | None:
    parts = (arg or "").split()
    if not parts or not _is_id(parts[0]):
        return None
    return int(parts[0]), parts[1:]


def _is_id(s: str) -> bool:
    return s.isascii() and s.isdigit()  # "²" 같은 유니코드 숫자 거부


def _account(ectx: executor.ExecContext, pa: dict | None):
    return next((x for x in ectx.accounts if pa and x.id == pa["account_id"]), None)


def _decide_and_execute(
    ctx: CommandContext, ectx: executor.ExecContext, aid: int, confirm: str | None
) -> str:
    """승인 결정(선점) → 데몬 안이라 바로 집행 → 결과 문구."""
    pa = xledger.get_approval(ctx.conn, aid)
    d = approvals.decide(
        ctx.conn, aid, "approve", channel="telegram", actor=ctx.actor, confirm=confirm,
        now=ctx.now(), accounts=ectx.accounts,
    )  # fmt: skip
    if not d["ok"]:
        return messages.failure(f"승인 실패 #{aid}", "", d["error"])
    r = approvals.execute(ectx, aid)
    order = r.get("order") or {}
    if r["ok"]:
        acct = _account(ectx, pa)
        line = approval_line(pa, acct.env if acct is not None else None) if pa else f"#{aid}"
        return messages.event(
            f"승인 실행 #{aid}", line, f"주문번호: {order.get('order_no') or '-'}"
        )
    if r.get("unknown"):
        # 응답 없음 — executor 가 이미 [주문 상태 미확인] 을 보냈다(실패로 덧알리면 재주문 유도)
        return f"#{aid} 주문 상태 미확인 — 앞선 알림을 확인하세요"
    err = r.get("error") or order.get("message") or r.get("reason") or "알 수 없음"
    return messages.failure(f"승인 실패 #{aid}", "", err)


def _reject(ctx: CommandContext, ectx: executor.ExecContext, aid: int) -> str:
    """거부 결정. 되면 `[거부]` 알림 1줄(`reject_notice`)을 남긴다 — 알림 기록(notices)이 승인과
    같이 거부도 갖게(T5-R1). 웹 거부는 큐 `approval_notice` 가 같은 문구를 보낸다(M12)."""
    pa = xledger.get_approval(ctx.conn, aid)
    d = approvals.decide(
        ctx.conn, aid, "reject", channel="telegram", actor=ctx.actor, confirm=None,
        now=ctx.now(), accounts=ectx.accounts,
    )  # fmt: skip
    if d["ok"]:
        if pa is not None:
            acct = _account(ectx, pa)
            send(ctx.notifier, reject_notice(pa, acct.env if acct is not None else None))
        return f"#{aid} 거부했어요"
    return messages.failure(f"거부 실패 #{aid}", "", d["error"])


def handle_approve(ctx: CommandContext, arg: str | None) -> str:
    parsed = _parse(arg)
    if parsed is None:
        return "사용법: /approve <승인 id>"
    aid, extra = parsed
    ectx = _exec_ctx(ctx)
    if ectx is None:
        return messages.event(f"승인 실패 #{aid}", NO_PORT)
    acct = _account(ectx, xledger.get_approval(ctx.conn, aid))
    real = acct is not None and acct.env != "paper"
    if real and messages.REAL_CONFIRM not in extra:
        # 실전 계좌 승인은 확인어를 붙여야 한다 — 웹의 확인창과 같은 한 번 더 묻기
        return messages.event(
            f"확인 필요 #{aid}",
            f"{acct.id} 은(는) 실전 계좌예요 — 실제 돈으로 주문해요.",
            f"진행하려면: /approve {aid} {messages.REAL_CONFIRM}",
        )
    confirm = acct.id if real else None
    return _decide_and_execute(ctx, ectx, aid, confirm)


def handle_reject(ctx: CommandContext, arg: str | None) -> str:
    parsed = _parse(arg)
    if parsed is None:
        return "사용법: /reject <승인 id>"
    aid, _ = parsed
    ectx = _exec_ctx(ctx)
    if ectx is None:
        return messages.event(f"거부 실패 #{aid}", NO_PORT)
    return _reject(ctx, ectx, aid)


def _ask(ctx: CommandContext, ectx: executor.ExecContext, aid: int) -> str:
    """실전 1단계: 아직 결정 전인 승인이면 계좌 id 를 실은 확인 버튼을 새 메시지로 보낸다.
    여기서는 결정·주문하지 않는다."""
    pa = xledger.get_approval(ctx.conn, aid)
    if pa is None or pa["status"] != "pending" or pa.get("decided_channel"):
        state = "없음" if pa is None else (pa.get("decided_channel") and "결정됨") or pa["status"]
        why = f"승인 요청 #{aid} 을 승인할 수 없음({state})"
        return messages.failure(f"승인 실패 #{aid}", "", why)
    acct = _account(ectx, pa)
    if acct is None:
        return messages.failure(f"승인 실패 #{aid}", "", f"계좌 {pa['account_id']} 가 설정에 없음")
    buttons = real_confirm_button(aid, acct.id)
    if len(buttons[0][0][1].encode("utf-8")) > CALLBACK_DATA_MAX:
        return f"계좌 id 가 길어 확인 버튼을 못 만들어요 — /approve {aid} {messages.REAL_CONFIRM}"
    text = messages.event(
        f"실전 승인 확인 #{aid}",
        approval_line(pa, acct.env),
        "실제 돈으로 주문해요. 아래 버튼을 누르면 바로 주문이 나가요.",
    )
    send(ctx.notifier, Message(text, kind="approval", account_id=acct.id, buttons=buttons))
    return f"#{aid} 실전 확인 버튼을 보냈어요"


def handle_callback(ctx: CommandContext, data: str) -> str:
    """`apv:<id>:yes|no|ask` 또는 `apv:<id>:yes:<계좌 id>`(실전 확인). 형식이 틀리면 거부."""
    parts = data.split(":", 3)
    if (
        len(parts) not in (3, 4)
        or parts[0] != APPROVAL_PREFIX
        or not _is_id(parts[1])
        or parts[2] not in VERBS
        or (len(parts) == 4 and (parts[2] != "yes" or not parts[3]))
    ):
        return BAD_BUTTON
    aid, verb = int(parts[1]), parts[2]
    ectx = _exec_ctx(ctx)
    if ectx is None:
        return messages.event(f"승인 실패 #{aid}", NO_PORT)
    if verb == "no":
        return _reject(ctx, ectx, aid)
    if verb == "ask":
        return _ask(ctx, ectx, aid)
    return _decide_and_execute(ctx, ectx, aid, parts[3] if len(parts) == 4 else None)


TELEGRAM_COMMANDS = (
    TelegramCommand("approve", False, "/approve <id> [실전] — 승인 요청 실행", handle_approve),
    TelegramCommand("reject", False, "/reject <id> — 승인 요청 거부", handle_reject),
)

TELEGRAM_CALLBACKS = (TelegramCallback(APPROVAL_PREFIX, handle_callback),)
