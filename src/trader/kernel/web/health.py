"""`/system/health` 어댑터: 판정은 `kernel/ops` 가 하고 여기서는 원천만 고른다.

기본은 데몬 `ops_check` 가 적은 control `ops_result`(그 `at` 이 `CACHE_MAX_AGE_FACTOR × 간격`
— 결과에 함께 적힌 실제 `interval_s`, 없으면 `OPS_CHECK.interval_s` — 안이고 데몬이 살아
있으며 하트비트가 멈추지 않았을 때). 그 밖·`live` 면 이 프로세스가 직접 `check()` 를 돈다
(결과는 적지 않는다).
응답 모양 `{level: ok|warn|fail, reasons, checked_at, version, features}` 는 그대로다."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from importlib import metadata
from typing import Any

from trader.kernel.daemon.heartbeat import read_status
from trader.kernel.ops import check, latest, to_web
from trader.kernel.ops.jobs import CACHE_MAX_AGE_FACTOR, OPS_CHECK, build_context

logger = logging.getLogger("trader.web")


def version() -> str:
    try:
        return metadata.version("trader")
    except metadata.PackageNotFoundError:
        return "0.2.0"


def _fresh(cached: dict | None, now: datetime) -> bool:
    """간격은 데몬이 결과와 함께 적은 `interval_s`(웹 deps 에는 schedule 이 없다). 없거나 잘못된
    값이면 `OPS_CHECK.interval_s` 기본값으로 본다(M-2)."""
    if not cached:
        return False
    try:
        at = datetime.fromisoformat(str(cached["at"]))
    except (KeyError, TypeError, ValueError):
        return False
    if at.tzinfo is None:
        return False
    try:
        interval = int(cached.get("interval_s") or 0)
    except (TypeError, ValueError):
        interval = 0
    if interval <= 0:
        interval = int(OPS_CHECK.interval_s or 0)
    age = (now - at).total_seconds()
    return 0 <= age < CACHE_MAX_AGE_FACTOR * interval


def _daemon_up(deps: Any, now: datetime) -> bool:
    """캐시는 데몬이 살아 있고 하트비트가 멈추지 않았을 때만 믿는다(죽은 직후의 ok 를 내지 않게)."""
    st = read_status(deps.state, now, [])
    return bool(st["alive"]) and not st["stale"]


def _ledger_failed(deps: Any, now: datetime, err: Exception, features: list[str]) -> dict:
    """원장을 못 읽으면 데몬 생존(잠금 파일)만 보고 원장 사유를 warn 으로 더한다."""
    reasons = []
    if not read_status(deps.state, now, [])["alive"]:
        reasons.append({"area": "jobs", "level": "fail", "text": "데몬이 꺼져 있어요"})
    reasons.append({"area": "ledger", "level": "warn", "text": f"원장 조회 실패: {err}"})
    return {
        "level": reasons[0]["level"],
        "reasons": reasons,
        "checked_at": now.isoformat(),
        "version": version(),
        "features": features,
    }


def evaluate(deps: Any, *, live: bool = False) -> dict:
    """데몬 캐시가 신선하면 그것을, 아니면 직접 판정한 결과를 웹 모양으로."""
    now = deps.now()
    features = [f.id for f in deps.features]
    try:
        with closing(deps.conn_factory()) as conn:
            cached = None if live else latest(conn)
            if _fresh(cached, now) and _daemon_up(deps, now):
                result = cached
            else:
                ctx = build_context(
                    conn=conn, settings=deps.settings, state=deps.state, now=now,
                    ports=deps.ports, features=deps.features, schedule=None,
                    markets=list(getattr(deps.settings, "markets", None) or []),
                )  # fmt: skip
                result = check(ctx)
    except sqlite3.Error as e:
        logger.warning("헬스 원장 조회 실패: %s", e)
        return _ledger_failed(deps, now, e, features)
    return to_web(result, version=version(), features=features)
