"""텔레그램 명령 디스패처. 명령 집합은 기능이 `Feature.telegram_commands` 로 등록하고,
커널은 내장 `/status`(기능의 `status_sections` 를 모아 보여 줌)와
`/run`(큐 요청)만 가진다. CLI·데몬 공용."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, replace
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
RUN = "run"  # 커널 내장 명령 — 큐에 작업 요청을 넣는다
BUILTINS = (STATUS, RUN)


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
    actor: str | None = None  # 명령을 보낸 사람(제어 변경 기록용; poll_once 가 업데이트마다 채운다)


def registry(features: list[Feature]) -> dict[str, TelegramCommand]:
    """기능이 등록한 명령 표. 이름이 겹치거나 내장 `status` 를 등록하면 RegistryError."""
    out: dict[str, TelegramCommand] = {}
    for f in features:
        for cmd in f.telegram_commands:
            if cmd.name in BUILTINS:
                raise RegistryError(
                    f"텔레그램 명령 '{cmd.name}' 는 커널 내장이라 등록할 수 없어요: {f.id}"
                )
            if cmd.name in out:
                raise RegistryError(f"텔레그램 명령 이름 중복: {cmd.name} ({f.id})")
            out[cmd.name] = cmd
    return out


def help_line(features: list[Feature]) -> str:
    parts = ["/status", "/run <작업> [KR|US] [종목]"]
    parts += [c.help_line for c in registry(features).values() if c.help_line]
    return " · ".join(parts)


def status_message(ctx: CommandContext) -> str:
    lines: list[str] = []
    for f in ctx.features:
        for sec in f.status_sections:
            try:
                lines.extend(sec.render(ctx))
            except Exception as e:  # noqa: BLE001 - 절 하나의 실패가 나머지 절을 가리지 않는다
                logger.exception("상태 절 조회 실패: %s", sec.title)
                lines.append(f"- {sec.title}: 조회 실패({type(e).__name__}: {e})")
    return messages.event("상태", *(lines or ["등록된 상태 절 없음"]))


def run_message(ctx: CommandContext, arg: str | None) -> str:
    """`/run <작업> [KR|US] [종목]` — 데몬 큐에 요청을 넣는다(실행은 데몬이 순서대로)."""
    from trader.kernel.cli.jobs_cmd import daemon_running
    from trader.kernel.daemon import queue as jobq

    kinds = jobq.kinds_of(ctx.features)
    tokens = (arg or "").split()
    if not tokens:
        return f"사용법: /run <작업> [KR|US] [종목]\n작업: {', '.join(sorted(kinds))}"
    kind, params = tokens[0], {}
    for t in tokens[1:]:
        params["market" if t.upper() in ("KR", "US") else "ticker"] = t.upper()
    if not daemon_running():
        return "거부: 데몬이 꺼져 있어 요청을 실행할 주체가 없어요 (`uv run trader daemon`)"
    try:
        rid = jobq.enqueue(ctx.conn, kinds, kind, params, source="telegram", now=ctx.now())
    except ValueError as e:  # 알 수 없는 작업·파라미터 오류·중복(DuplicateRequest)
        return f"거부: {e}"
    return messages.event(f"요청 #{rid}", f"{jobq.label(kinds, kind)} — 데몬이 순서대로 실행해요")


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
        if cmd is None and name not in BUILTINS:
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
            if name == STATUS:
                reply = status_message(ctx)
            elif name == RUN:
                reply = run_message(ctx, arg)
            else:
                actor = str(getattr(u, "sender_id", "") or getattr(u, "chat_id", "") or "") or None
                reply = cmd.handler(replace(ctx, actor=actor), arg)
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
