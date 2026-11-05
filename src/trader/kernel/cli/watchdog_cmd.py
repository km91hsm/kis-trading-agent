"""`trader watchdog`: 외부 감시 1회 — 판정 결과를 JSON 한 줄로."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer

from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.platform import paths


@config_errors
def watchdog(
    quiet: bool = typer.Option(False, "--quiet", help="알림 없이 판정만 출력(점검용)"),
) -> None:
    """외부 감시 1회: 데몬 생존·하트비트·운영 점검을 지금 판정해 주의 이상이면 텔레그램.
    5분 간격 등록용. 감시 기록(state/watchdog.json) 외에는 상태를 바꾸지 않는다."""
    from trader.kernel.notify.telegram import make_notifier
    from trader.kernel.ops import watchdog as watchdog_mod
    from trader.kernel.ops.jobs import build_context

    b = boot(
        settings=True, conn=True, secrets=True, ports=True, schedule=True, log_name="watchdog"
    )
    try:
        notifier = (
            None if quiet else make_notifier(b.secrets, enabled=b.settings.notify.enabled)
        )
        ctx = build_context(
            conn=b.conn, settings=b.settings, state=paths.state_dir(), now=datetime.now(UTC),
            ports=b.ports, features=b.features, schedule=b.schedule,
            markets=list(b.settings.markets),
        )  # fmt: skip
        out = watchdog_mod.run_once(ctx, notifier)
    finally:
        b.conn.close()
    typer.echo(json.dumps(out, ensure_ascii=False))
