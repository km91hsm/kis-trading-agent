"""briefing 잡: 평일 07:20 KST 아침 브리핑. `per_market=False`, 본문이 휴장일·일시정지를 본다."""

from __future__ import annotations

from trader.features.briefing.briefing import run
from trader.features.saveticker import api as saveticker_api
from trader.kernel.config import KST
from trader.kernel.registry.types import JobSpec


def briefing_body(deps, job=None) -> dict:
    """아침 브리핑 → 텔레그램. KR 시장이 아니거나 일시정지·KR 휴장일이면 건너뛴다."""
    if "KR" not in deps.markets:
        return {"skipped": "KR 시장 아님"}
    acc = deps.ports["accounts"]
    conn = deps.conn_factory()
    try:
        if acc.paused(conn):
            return {"skipped": "일시정지"}
        now = deps.now()
        if acc.holiday_fn()("KR", now.astimezone(KST).date()):
            return {"skipped": "휴장일"}
        root = saveticker_api.resolve_dir(saveticker_api.load_cfg(registry=deps.registry).dir)
        return run(
            conn,
            deps.settings,
            acc.accounts(),
            now=now,
            notifier=deps.notifier,
            market_client=acc.market_client(),
            saveticker_root=root,
            cancel=deps.stop_event,
        )
    finally:
        conn.close()


MORNING_BRIEFING = JobSpec(
    id="morning_briefing", kind="briefing", trigger="cron", per_market=False,
    schedule_key="briefing", interval_s=None, enabled=lambda s: bool(s.briefing.enabled),
    gate="none", heavy=False, misfire="default", catch_up="notify", start_soon_s=0,
    body=briefing_body, days="mon-fri",
)  # fmt: skip
