"""spot 데몬 잡: `approvals_expire` — 만료 시각이 지난 pending 승인을 expired 로."""

from __future__ import annotations

from trader.features.spot import executor
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.registry.types import JobSpec


def expire_body(ctx, job) -> dict:
    """boot 없이 데몬 의존성으로 돈다 — 만료 처리는 계좌·브로커가 필요 없다(원장 + 알림만)."""
    conn = ctx.conn_factory()
    try:
        ectx = executor.ExecContext(
            settings=ctx.settings,
            accounts=[],
            conn=conn,
            brokers={},
            policy=executor._no_policy,
            paused=executor._not_paused,
            notifier=ctx.notifier or NullNotifier(),
            state=ctx.state,
            now=ctx.now,
        )
        return {"expired": executor.expire_approvals(ectx)}
    finally:
        conn.close()


APPROVALS_EXPIRE = JobSpec(
    id="approvals_expire", kind="approvals", trigger="interval", per_market=False,
    schedule_key=None, interval_s=60, enabled=None, gate="none", heavy=False,
    misfire="idempotent", catch_up="none", start_soon_s=30, body=expire_body,
)  # fmt: skip
