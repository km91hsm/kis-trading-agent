"""`trader watchdog`: 외부 감시 1회 — 판정 결과를 JSON 한 줄로."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import typer

from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.config import ConfigError, memo_load
from trader.kernel.platform import paths

logger = logging.getLogger("trader.ops")


@config_errors
def watchdog(
    quiet: bool = typer.Option(False, "--quiet", help="알림 없이 판정만 출력(점검용)"),
) -> None:
    """외부 감시 1회: 데몬 생존·하트비트·운영 점검을 지금 판정해 주의 이상이면 텔레그램.
    5분 간격 등록용. 감시 기록(state/watchdog.json) 외에는 상태를 바꾸지 않는다."""
    from trader.kernel.notify.telegram import make_notifier
    from trader.kernel.ops import watchdog as watchdog_mod
    from trader.kernel.ops.jobs import build_context

    b = boot(settings=True, conn=True, secrets=True, ports=True, log_name="watchdog")
    try:
        # schedule 오류로 데몬이 못 뜨는 바로 그때도 감시는 돌아야 한다 — 잡 표 없이 판정
        try:
            schedule = memo_load("schedule", b.registry)
        except ConfigError as e:
            logger.warning("schedule 설정 오류, 잡 표 없이 감시: %s", e)
            schedule = None
        notifier = (
            None if quiet else make_notifier(b.secrets, enabled=b.settings.notify.enabled)
        )
        ctx = build_context(
            conn=b.conn, settings=b.settings, state=paths.state_dir(), now=datetime.now(UTC),
            ports=b.ports, features=b.features, schedule=schedule,
            markets=list(b.settings.markets),
        )  # fmt: skip
        out = watchdog_mod.run_once(ctx, notifier)
    finally:
        b.conn.close()
    typer.echo(json.dumps(out, ensure_ascii=False))
