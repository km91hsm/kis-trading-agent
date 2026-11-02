"""텔레그램 명령 디스패처. 명령 집합은 기능이 `Feature.telegram_commands` 로 등록하고,
커널은 내장 `/status`(기능의 `status_sections` 를 모아 보여 줌)만 가진다. CLI·데몬 공용."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from trader.kernel.ledger.control import get_flag, set_flag
from trader.kernel.notify import messages
from trader.kernel.notify.telegram import Notifier, parse_command
from trader.kernel.registry.types import Feature, RegistryError, TelegramCommand

logger = logging.getLogger("trader.notify")

OFFSET_FLAG = "telegram_offset"
HELP_FLAG = "telegram_help_at"  # 마지막 도움말 발송 시각(폴링 사이에도 유지)
HELP_EVERY_S = 60
STATUS = "status"  # 커널 내장 명령 — 기능이 같은 이름을 등록할 수 없다


@dataclass
class CommandContext:
    conn: sqlite3.Connection
    settings: Any
    state: Path | None
    now: Callable[[], datetime]
    notifier: Notifier
    features: list[Feature]
    echo: Callable[[str], None] = lambda s: None
    ports: dict[str, Any] = field(default_factory=dict)  # 기능이 내놓은 포트(이름 → 구현)


def registry(features: list[Feature]) -> dict[str, TelegramCommand]:
    """기능이 등록한 명령 표. 이름이 겹치거나 내장 `status` 를 등록하면 RegistryError."""
    out: dict[str, TelegramCommand] = {}
    for f in features:
        for cmd in f.telegram_commands:
            if cmd.name == STATUS:
                raise RegistryError(
                    f"텔레그램 명령 '{STATUS}' 는 커널 내장이라 등록할 수 없어요: {f.id}"
                )
            if cmd.name in out:
                raise RegistryError(f"텔레그램 명령 이름 중복: {cmd.name} ({f.id})")
            out[cmd.name] = cmd
    return out


def help_line(features: list[Feature]) -> str:
    parts = ["/status"]
    parts += [c.help_line for c in registry(features).values() if c.help_line]
    return " · ".join(parts)


def status_message(ctx: CommandContext) -> str:
    lines: list[str] = []
    for f in ctx.features:
        for sec in f.status_sections:
            lines.extend(sec.render(ctx))
    return messages.event("상태", *(lines or ["등록된 상태 절 없음"]))


def _help_due(ctx: CommandContext) -> bool:
    """모르는 /xxx 마다 도움말을 보내지 않는다 — 분당 1회. 시각은 control 에 남긴다."""
    now = ctx.now()
    last = get_flag(ctx.conn, HELP_FLAG)
    if last:
        try:
            if (now - datetime.fromisoformat(str(last))).total_seconds() < HELP_EVERY_S:
                return False
        except ValueError:
            pass
    set_flag(ctx.conn, HELP_FLAG, now.isoformat())
    return True


def poll_once(ctx: CommandContext, receiver: Any) -> dict:
    """getUpdates 1회: 명령을 처리하고 오프셋을 control `telegram_offset` 에 저장한다."""
    conn = ctx.conn
    cmds = registry(ctx.features)
    offset = get_flag(conn, OFFSET_FLAG)
    updates = receiver.poll(offset)
    # 승인 만료는 여기서 하지 않는다: `approvals list`·`approvals_expire` 잡이 바꾸고, 승인 실행
    # (`execution.core.approve`)도 만료된 요청을 스스로 거부한다
    handled = 0
    failed = 0
    for u in updates:
        parsed = parse_command(u.text)
        if parsed is None:
            if u.text.strip().startswith("/"):
                # 오타·모르는 명령을 조용히 버리지 않는다 — 단 분당 1회
                if _help_due(ctx):
                    ctx.notifier.send(messages.event("명령 안내", help_line(ctx.features)))
                set_flag(conn, OFFSET_FLAG, int(u.update_id) + 1)
            continue
        name, arg = parsed
        cmd = cmds.get(name)
        if cmd is None and name != STATUS:
            if _help_due(ctx):
                ctx.notifier.send(messages.event("명령 안내", help_line(ctx.features)))
            set_flag(conn, OFFSET_FLAG, int(u.update_id) + 1)
            continue
        handled += 1
        if not getattr(u, "trusted", True) and not (name == STATUS or (cmd and cmd.untrusted_ok)):
            # 그룹 채팅의 아무 구성원이 승인·재개·모드를 바꾸지 못하게
            ctx.notifier.send(
                messages.event(
                    "명령 거부",
                    f"/{name} 은 1:1 채팅 또는 허용 사용자(TELEGRAM_ALLOWED_USERS)만 쓸 수 있어요",
                )
            )
            set_flag(conn, OFFSET_FLAG, int(u.update_id) + 1)
            continue
        try:
            reply = status_message(ctx) if name == STATUS else cmd.handler(ctx, arg)
            ctx.notifier.send(reply)
            ctx.echo(f"{name} {arg or ''}".strip())
        except Exception as e:  # noqa: BLE001 - 명령 하나의 실패가 나머지·오프셋을 막지 않는다
            failed += 1
            logger.exception("텔레그램 명령 처리 실패: %s", u.text)
            try:
                ctx.notifier.send(messages.failure("명령 실패", f"{u.text}", e))
            except Exception:  # noqa: BLE001
                logger.debug("명령 실패 알림 실패", exc_info=True)
        # 업데이트마다 오프셋을 바로 전진시킨다 — 뒤 명령이 예외로 끊겨도 같은 명령을 다시 받지 않음
        set_flag(conn, OFFSET_FLAG, int(u.update_id) + 1)
    # 걸러진(다른 채팅) 업데이트까지 포함한 최대 id 로 오프셋을 전진시킨다
    ids = [u.update_id for u in updates]
    last_raw = getattr(receiver, "last_update_id", None)
    if last_raw is not None:
        ids.append(int(last_raw))
    if ids:
        set_flag(conn, OFFSET_FLAG, max(ids) + 1)
    ctx.echo(f"수신 {len(updates)}건, 명령 {handled}건" + (f", 실패 {failed}건" if failed else ""))
    return {"received": len(updates), "handled": handled, "failed": failed}
