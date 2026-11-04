"""데몬 내장 잡 `ops_check`: 점검(`check`) → 결과 보관(control `ops_result`) → 격상 알림.
웹 `/system/health` 도 같은 문맥 조립(`build_context`)으로 직접 판정한다."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from trader.kernel.config import ConfigError
from trader.kernel.daemon import jobs as daemon_jobs
from trader.kernel.daemon.jobs import ScheduledJob
from trader.kernel.ops.health import check, escalate, store
from trader.kernel.ops.types import OpsContext
from trader.kernel.registry.types import Feature, JobSpec

logger = logging.getLogger("trader.ops")

CACHE_MAX_AGE_FACTOR = 2  # 데몬 결과가 `2 × ops_check 간격` 안이면 웹이 그대로 쓴다


class _AnyGates:
    """점검용 잡 펼치기에서 게이트 검사를 건너뛴다(게이트 구현은 데몬 부트만 안다)."""

    def __bool__(self) -> bool:
        return True

    def __contains__(self, name: object) -> bool:
        return True


_PROBE_CTX = type("_ProbeCtx", (), {"gates": _AnyGates()})()


def schedule_jobs(
    features: Sequence[Feature], *, settings: Any, schedule: Any, markets: list[str]
) -> list[ScheduledJob]:
    """데몬이 올리는 잡 표(기능 + 커널 잡). 하트비트의 놓친 잡 판정용이라 선언 오류면 빈 목록."""
    from trader.kernel.daemon.catalog import with_kernel  # builtin_jobs → ops.jobs 순환 회피

    try:
        return daemon_jobs.build(
            with_kernel(features), schedule=schedule, settings=settings, markets=list(markets),
            ctx=_PROBE_CTX,
        )  # fmt: skip
    except ConfigError as e:
        logger.warning("점검용 잡 표를 만들지 못함: %s", e)
        return []


def build_context(
    *,
    conn: sqlite3.Connection,
    settings: Any,
    state: Path,
    now: datetime,
    ports: Mapping[str, Any],
    features: Sequence[Feature],
    schedule: Any,
    markets: list[str],
) -> OpsContext:
    """영역 문맥. schedule 이 None 이면(웹) 잡 표 없이 — 놓친 잡 항목은 데몬 결과에만 나온다."""
    jobs = (
        []
        if schedule is None
        else schedule_jobs(features, settings=settings, schedule=schedule, markets=markets)
    )
    return OpsContext(
        conn=conn, settings=settings, state=state, now=now, ports=dict(ports or {}),
        jobs=jobs, features=list(features),
    )  # fmt: skip


def ops_check_body(deps: Any, job: Any = None) -> dict:
    """점검 1회: 결과를 control `ops_result` 에 적고, 나빠졌으면 텔레그램으로 한 번 알린다."""
    conn = deps.conn_factory()
    try:
        ctx = build_context(
            conn=conn, settings=deps.settings, state=deps.state, now=deps.now(),
            ports=deps.ports, features=deps.features, schedule=deps.schedule,
            markets=deps.markets,
        )  # fmt: skip
        result = check(ctx)
        store(conn, result)
        msg = escalate(conn, result)
    finally:
        conn.close()
    notified = False
    if msg and deps.notifier is not None:
        deps.notifier.send(msg)
        notified = True
    return {"level": result["level"], "items": len(result["items"]), "notified": notified}


OPS_CHECK = JobSpec(
    id="ops_check", kind="ops_check", trigger="interval", per_market=False,
    schedule_key="ops_check.interval_s", interval_s=1800, enabled=None, gate="none",
    heavy=False, misfire="default", catch_up="none", start_soon_s=60, body=ops_check_body,
)  # fmt: skip
