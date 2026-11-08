"""scalp 잡: `{market}_scalp` 분 단위 틱(감시 주기 `{market}.monitor.interval_s`).

게이트는 `none`(Ruling A4·T7-R1) — 창 밖·주말·휴장·일시정지·꺼짐에서도 본문이 돌아 `{"skipped": …}`
를 돌려주고 그날 모아 둔 outbox 를 보낸다(게이트가 본문을 막으면 묶음 알림이 상태 파일에 갇힌다).
창·주말·휴장은 accounts 가 공급한 monitor 게이트를 본문이 직접 부르고, 일시정지는 따로 본다
(monitor 게이트는 일시정지를 보지 않는다)."""

from __future__ import annotations

import logging
from typing import Any

from trader.features.scalp import engine, switch
from trader.features.scalp.judge import judge_for, shutdown_judge
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.registry.types import JobSpec
from trader.kernel.session.run_store import run_date_for

logger = logging.getLogger("trader.scalp")


def scalp_body(ctx: Any, job: Any) -> dict:
    """1틱. 켜진 계좌 없음·창 밖·휴장·일시정지·시세 클라이언트 없음이면 skipped(잡 표에 보임)."""
    market = job.market or "KR"
    settings = ctx.settings
    acc = (ctx.ports or {}).get("accounts")
    if acc is None:
        return {"error": "accounts 포트 없음"}
    gate = (getattr(ctx, "gates", None) or {}).get("monitor")
    if gate is None:  # 창·휴장을 거를 수 없으면 틱을 돌리지 않는다(fail-closed)
        return {"skipped": "monitor 게이트 없음"}
    reason = gate(job)
    if reason:
        return {"skipped": reason}
    conn = ctx.conn_factory()
    try:
        if acc.paused(conn):
            return {"skipped": "일시정지"}
        if not switch.any_enabled(conn, settings, acc, market):
            return {"skipped": "단타 켜진 계좌 없음"}
        market_client = acc.market_client()
        if market_client is None:
            return {"skipped": "시세 클라이언트 없음"}
        ms = getattr(ctx.schedule, market.lower()).monitor
        sctx = engine.ScalpContext(
            settings=settings,
            conn=conn,
            ports=ctx.ports,
            market_client=market_client,
            open_hm=ms.start,
            close_hm=ms.end,
            notifier=ctx.notifier or NullNotifier(),
            now=ctx.now,
            state=ctx.state,
            dry_run=bool(ctx.dry_run),
            exchanges={},  # 유니버스는 scan 소유 — engine.candidates 가 거래소를 찾는다
            judge=judge_for(ctx) if settings.scalp.judge else None,
        )
        return engine.tick(sctx, market=market, today=run_date_for(market, ctx.now()))
    finally:
        conn.close()


def scalp_job(ctx: Any, job: Any) -> dict:
    """틱이 건너뛰어졌으면 그날 모아 둔 진입·청산 묶음만 보낸다(묶음 알림이 갇히지 않게)."""
    market = job.market or "KR"
    out = scalp_body(ctx, job)
    if isinstance(out, dict) and out.get("skipped"):
        try:
            engine.flush_outbox(
                ctx.state,
                run_date_for(market, ctx.now()),
                market,
                notifier=ctx.notifier or NullNotifier(),
                now=ctx.now(),
                settings=ctx.settings,
                dry_run=bool(ctx.dry_run),
            )
        except Exception:  # noqa: BLE001 - 알림 정리가 잡 결과를 바꾸지 않는다
            logger.warning("단타 묶음 정리 실패 %s", market, exc_info=True)
    return out


def judge_teardown(ctx: Any) -> None:
    """데몬 종료: 판정자 스레드 풀을 닫는다(대기 중 판정은 버린다)."""
    shutdown_judge()


SCALP_TICK = JobSpec(
    id="{market}_scalp", kind="scalp", trigger="interval", per_market=True,
    schedule_key="{market}.monitor.interval_s", interval_s=60, enabled=None, gate="none",
    heavy=False, misfire="default", catch_up="none", start_soon_s=30,
    body=scalp_job, teardown=judge_teardown,
)  # fmt: skip
