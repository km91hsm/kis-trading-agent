"""`/fo` 텔레그램 명령: 파생 포지션·오늘 결정·한도 상태(원장만 읽음, `trader fo status` 와 같은 줄).
읽기 전용이라 `untrusted_ok=True`."""

from __future__ import annotations

from typing import Any

from trader.features.fo import status
from trader.kernel.notify import messages
from trader.kernel.registry.types import TelegramCommand


def fo_cmd(ctx: Any, arg: str | None) -> str:
    return messages.event("파생", *status.lines(ctx.conn, ctx.now()))


FO_CMD = TelegramCommand("fo", True, "/fo(파생 포지션)", fo_cmd)
