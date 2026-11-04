"""accounts 텔레그램 명령(/pause·/resume·/mode)과 /status 의 계좌 절."""

from __future__ import annotations

from typing import Any

from trader.features.accounts import modes
from trader.features.accounts.settings import Account, load_accounts
from trader.kernel.notify import messages
from trader.kernel.registry.types import StatusSection, TelegramCommand


def _mode_label(conn, account: Account) -> str:
    """STATUS·웹과 같은 실효 모드 (실전 auto + 확인 기록 없음 = off, 실전 파생 = off, 강등 표시)."""
    mode, note = modes.account_mode(conn, account)
    env = messages.ENV.get(account.env, account.env)
    return f"{account.id}({env}) {messages.MODE.get(mode, mode)}" + (" · 강등" if note else "")


def pause(ctx: Any, arg: str | None) -> str:
    modes.set_paused(ctx.conn, True, channel="telegram", actor=ctx.actor)
    return messages.event("일시정지", "모든 계좌의 주문을 멈췄어요")


def resume(ctx: Any, arg: str | None) -> str:
    modes.set_paused(ctx.conn, False, channel="telegram", actor=ctx.actor)
    return messages.event("재개", "주문을 다시 내요")


def mode(ctx: Any, arg: str | None) -> str:
    parts = (arg or "").split()
    if len(parts) != 2:
        return "사용법: /mode <계좌 id> <auto|approve|off>"
    acct_id, new_mode = parts
    acct = next((a for a in load_accounts() if a.id == acct_id), None)
    if acct is None:
        return f"알 수 없는 계좌: {acct_id}"
    if acct.env == "real" and new_mode != "off":
        return (
            f"거부: 실전 계좌 {acct.id} 는 텔레그램으로 off(비상 정지)만 가능합니다. "
            "상향은 CLI `trader accounts mode <계좌> approve|auto --confirm LIVE` 로만"
        )
    try:
        modes.set_account_mode(
            ctx.conn, acct, new_mode, channel="telegram", actor=ctx.actor
        )
    except (ValueError, modes.ConfirmationRequired) as e:
        return f"거부: {e}"
    return messages.mode_changed(account=acct.id, mode=new_mode)


def render(ctx: Any) -> list[str]:
    paused = "일시정지" if modes.is_paused(ctx.conn) else "실행 중"
    labels = ", ".join(_mode_label(ctx.conn, a) for a in load_accounts())
    return [f"{paused} · 계좌 모드: {labels}"]


TELEGRAM_COMMANDS = (
    TelegramCommand("pause", False, "/pause", pause),
    TelegramCommand("resume", False, "/resume", resume),
    TelegramCommand("mode", False, "/mode <계좌> <auto|approve|off>", mode),
)
STATUS_SECTION = StatusSection("계좌", render)
