"""`/fo` 텔레그램 명령: 인자 없으면 파생 포지션·오늘 결정·한도 상태(원장만 읽음, `trader fo status`
와 같은 줄 — 누구나). `/fo <계좌> on|off|기본` 은 계좌별 파생 스위치(웹 `/settings/fo` 와 같은
control `fo_enabled:<계좌>`, 이력 `telegram`) — 쓰기라 신뢰된 발신자만(`ctx.trusted`), 실전 파생
계좌는 켤 수 없다."""

from __future__ import annotations

from typing import Any

from trader.features.fo import status, switch
from trader.kernel.notify import messages
from trader.kernel.notify.commands import parse_toggle
from trader.kernel.registry.types import TelegramCommand

USAGE = "사용법: /fo — 상태 · /fo <계좌> on|off|기본 — 파생 신규 진입 켜기·끄기(모의 전용)"
SOURCE = {"control": "화면·텔레그램 설정", "yaml": "설정 파일", "default": "실전 기본 꺼짐"}


def fo_cmd(ctx: Any, arg: str | None) -> str:
    parts = (arg or "").split()
    if not parts:
        return messages.event("파생", *status.lines(ctx.conn, ctx.now()))
    ok, value = parse_toggle(parts[1]) if len(parts) == 2 else (False, None)
    if not ok:
        return USAGE
    if not getattr(ctx, "trusted", True):
        return messages.event("명령 거부", "/fo 켜기·끄기는 허용된 사용자만 할 수 있어요")
    port = (getattr(ctx, "ports", None) or {}).get("accounts")
    accts = [a for a in port.fo_accounts() if a.asset == "fo"] if port is not None else []
    acct = next((a for a in accts if a.id == parts[0]), None)
    if acct is None:
        return f"알 수 없는 파생 계좌: {parts[0]}"
    if acct.env != "paper" and value:
        return messages.event("명령 거부", f"실전 파생 계좌 {acct.id} 는 켤 수 없어요(항상 꺼짐)")
    on, source = switch.set_switch(ctx.conn, ctx.settings, acct, value, channel="telegram",
                                   actor=ctx.actor, at=ctx.now().isoformat())  # fmt: skip
    tail = "" if on else " · 보유분 감시·축소는 계속"
    return messages.event(
        "파생", f"{acct.id} 파생 신규 진입 {'켜짐' if on else '꺼짐'} · {SOURCE[source]}{tail}"
    )


FO_CMD = TelegramCommand("fo", True, "/fo [<계좌> on|off|기본](파생 상태·켜기·끄기)", fo_cmd)
