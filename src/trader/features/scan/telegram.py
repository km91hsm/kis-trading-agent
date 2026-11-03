"""scan 텔레그램 명령 `/watch add|remove|list <종목>` — 런타임 관심종목(control 플래그)."""

from __future__ import annotations

from trader.features.scan import watchlist
from trader.kernel.notify import messages
from trader.kernel.notify.commands import CommandContext
from trader.kernel.registry.types import TelegramCommand


def handle_watch(ctx: CommandContext, arg: str | None) -> str:
    conn = ctx.conn
    parts = (arg or "").split()
    sub = parts[0] if parts else "list"
    current = watchlist.get_watchlist(conn)
    try:
        if sub == "add" and len(parts) == 2:
            current = watchlist.set_watchlist(conn, [*current, parts[1]])
        elif sub == "remove" and len(parts) == 2:
            current = watchlist.remove_watch(conn, parts[1])
        elif sub != "list":
            return "사용법: /watch add|remove <ticker> | /watch list"
    except ValueError as e:
        return f"거부: {e}"
    return messages.event("관심종목", ", ".join(current) or "없음")


TELEGRAM_COMMANDS = (
    TelegramCommand("watch", False, "/watch add|remove|list <종목> — 관심종목", handle_watch),
)
