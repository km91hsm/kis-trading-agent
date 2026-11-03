"""커널 내장 잡: 텔레그램 폴링(+보내지 못한 알림 재전송). 커널은 기능 이름을 모른다."""

from __future__ import annotations

import logging
from typing import Any

from trader.kernel.notify import commands
from trader.kernel.notify.commands import CommandContext
from trader.kernel.notify.telegram import make_receiver
from trader.kernel.registry.types import Feature, JobSpec

logger = logging.getLogger("trader.daemon")


def telegram_body(deps: Any, job: Any = None) -> dict:
    """outbox 재전송(실패는 삼킴) → getUpdates 1회로 명령 처리. 수신 설정이 없으면 건너뛴다."""
    flush = getattr(deps.notifier, "flush_outbox", None)
    if flush is not None:
        try:
            flush(now=deps.now())
        except Exception as e:  # noqa: BLE001 - 재전송 실패가 명령 폴링을 막지 않는다
            logger.warning("알림 재전송 실패: %s", e)
    receiver = make_receiver(deps.secrets, enabled=deps.settings.notify.enabled)
    if receiver is None:
        return {"skipped": "텔레그램 미설정"}
    conn = deps.conn_factory()
    try:
        ctx = CommandContext(
            conn=conn, settings=deps.settings, state=deps.state, now=deps.now,
            notifier=deps.notifier, features=list(deps.features), echo=logger.info,
            ports=deps.ports,
        )  # fmt: skip
        return commands.poll_once(ctx, receiver)
    finally:
        conn.close()


TELEGRAM_POLL = JobSpec(
    id="telegram_poll", kind="telegram_poll", trigger="interval", per_market=False,
    schedule_key="telegram_poll.interval_s", interval_s=30, enabled=None, gate="none",
    heavy=False, misfire="default", catch_up="none", start_soon_s=10, body=telegram_body,
)  # fmt: skip
BUILTIN_JOBS: tuple[JobSpec, ...] = (TELEGRAM_POLL,)
BUILTIN_FEATURE = Feature(id="kernel_builtin", tier="service", jobs=BUILTIN_JOBS)
