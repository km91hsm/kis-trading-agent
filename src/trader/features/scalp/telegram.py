"""`/scalp [<계좌> on|off|기본]` 텔레그램 명령: 인자 없으면 계좌별 단타 스위치 상태, 있으면
그 계좌의 스위치(웹 `/settings/scalp` 와 같은 control `scalp_enabled:<계좌>`, 이력 `telegram`).
쓰기라 신뢰된 발신자만(`untrusted_ok=False`). 실전 켜기는 웹에서 계좌 id 확인으로만 —
텔레그램은 거부(끄기는 허용)."""

from __future__ import annotations

from typing import Any

from trader.features.scalp import switch
from trader.kernel.ledger.control import delete_flag, log_change, set_flag
from trader.kernel.notify import messages
from trader.kernel.notify.commands import parse_toggle
from trader.kernel.registry.types import TelegramCommand

USAGE = "사용법: /scalp — 계좌별 상태 · /scalp <계좌> on|off|기본 — 단타 켜기·끄기"
SOURCE = {"control": "화면·텔레그램 설정", "yaml": "설정 파일", "default": "실전 기본 꺼짐"}


def _accounts(ctx: Any) -> list[Any]:
    port = (getattr(ctx, "ports", None) or {}).get("accounts")
    return list(port.accounts()) if port is not None else []


def scalp_cmd(ctx: Any, arg: str | None) -> str:
    parts = (arg or "").split()
    accts = _accounts(ctx)
    if not parts:
        lines = []
        for a in accts:
            on, source = switch.stored(ctx.conn, ctx.settings, a)
            lines.append(f"- {a.id} · {'켜짐' if on else '꺼짐'} · {SOURCE[source]}")
        return messages.event("단타", *(lines or ["- 계좌 없음"]))
    ok, value = parse_toggle(parts[1]) if len(parts) == 2 else (False, None)
    if not ok:
        return USAGE
    acct = next((a for a in accts if a.id == parts[0]), None)
    if acct is None:
        return f"알 수 없는 계좌: {parts[0]}"
    if acct.env != "paper" and value:
        return messages.event(
            "명령 거부", f"실전 계좌 {acct.id} 단타 켜기는 웹 설정 화면에서 계좌 id 확인으로만 돼요"
        )
    key = switch.flag_key(acct.id)
    old = switch.stored(ctx.conn, ctx.settings, acct)
    if value is None:
        delete_flag(ctx.conn, key)
    else:
        set_flag(ctx.conn, key, value)
    new = switch.stored(ctx.conn, ctx.settings, acct)
    if old[0] != new[0]:
        log_change(ctx.conn, channel="telegram", actor=ctx.actor, key=key, old=old[0], new=new[0],
                   at=ctx.now().isoformat())  # fmt: skip
    tail = "" if new[0] else " · 열린 단타 포지션 청산은 계속"
    state = "켜짐" if new[0] else "꺼짐"
    return messages.event("단타", f"{acct.id} 단타 {state} · {SOURCE[new[1]]}{tail}")


SCALP_CMD = TelegramCommand(
    "scalp", False, "/scalp [<계좌> on|off|기본](단타 켜기·끄기)", scalp_cmd
)
