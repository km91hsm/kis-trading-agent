"""SSE `/api/events`: 지문 스냅샷이 바뀔 때만 `change`, 주기적으로 서버 시각을 담은 ping.

스냅샷 = 커널 키(`daemon` + `KERNEL_PROBES`) + 모든 `feature.sse_keys` 의 `probe(deps)`.
비동기 제너레이터라 스레드 풀을 점유하지 않는다(스냅샷 계산만 run_in_threadpool).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import closing
from typing import Any

import anyio
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from trader.kernel.daemon.heartbeat import read_status
from trader.kernel.web.deps import WebDeps

logger = logging.getLogger("trader.web")
router = APIRouter(tags=["events"])

_SHARED: dict[str, Any] = {"deps": None, "at": 0.0, "snap": None}
_SHARED_LOCK = threading.Lock()


def _daemon_fp(deps: WebDeps) -> dict:
    """데몬 지문: 생존 여부 + 하트비트 갱신 '분'. 틱이 몇 초마다 파일을 써도 change 는 분당 1회."""
    try:
        status = read_status(deps.state, deps.now(), [])
        return {"alive": bool(status["alive"]), "minute": (status.get("updated_at") or "")[:16]}
    except Exception:
        logger.warning("SSE daemon 지문 실패", exc_info=True)
        return {"alive": False, "minute": ""}


def _q(deps: WebDeps, sql: str) -> str:
    """지문용 한 줄 집계(행 하나의 값들을 ':' 로 잇는다). 표가 없으면(옛·빈 원장) 빈 문자열."""
    with closing(deps.conn_factory()) as conn:
        try:
            row = conn.execute(sql).fetchone()
        except sqlite3.OperationalError:
            return ""
    return ":".join("" if v is None else str(v) for v in tuple(row))


# 화면이 읽지 않는 내부 커서·TTL 플래그(매 수집·60초마다 값이 바뀐다) — control 지문에서 뺀다
# `killswitch_obs:*` 는 잔고를 볼 때마다(5분·계좌마다) 바뀌는 킬스위치 내부 관측값이다 — 지문에
# 넣으면 control 을 구독하는 화면 열댓 개가 그때마다 다시 읽는다
CONTROL_CURSOR_LIKE = (
    "telegram_offset",
    "news_trigger_at:%",
    "ws_session_busy:%",
    "fx_cache:%",
    "killswitch_obs:%",
)
_CONTROL_WHERE = " AND ".join(f"key NOT LIKE {p!r}" for p in CONTROL_CURSOR_LIKE)


def _usage_fp(deps: WebDeps) -> str:
    """구독 사용률 관측(세션 rate_limit_event·usage_probe) 건수·최대 id."""
    return _q(deps, "SELECT COUNT(*), MAX(id) FROM llm_rate_limits")


def _control_fp(deps: WebDeps) -> str:
    """control 플래그 (키, 값) 전체의 해시(커서류 제외). updated_at 은 1초 해상도라 같은 초 안의
    두 번째 변경을 놓치므로 값 자체를 본다 — 같은 값 다시 쓰기는 그대로 지문이 같다."""
    with closing(deps.conn_factory()) as conn:
        try:
            rows = conn.execute(
                f"SELECT key, value FROM control WHERE {_CONTROL_WHERE} ORDER BY key"
            ).fetchall()
        except sqlite3.OperationalError:
            return ""
    body = "\x1e".join(f"{r[0]}\x1f{r[1]}" for r in rows)
    return f"{len(rows)}:{hashlib.sha1(body.encode('utf-8')).hexdigest()[:16]}"


def _jobs_fp(deps: WebDeps) -> str:
    """작업 대기열 최대 id + 대기·실행 중 건수(progress 는 1초마다 바뀌어 넣지 않는다)."""
    return _q(
        deps,
        "SELECT COALESCE(MAX(id), 0), "
        "(SELECT COUNT(*) FROM job_requests WHERE status IN ('queued','running')) "
        "FROM job_requests",
    )


def _runs_fp(deps: WebDeps) -> str:
    """runs 건수·최대 id + 상태별 건수(새 행 없이 running → ok 로 바뀌어도 잡는다)."""
    head = _q(deps, "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM runs")
    groups = _q(
        deps,
        "SELECT GROUP_CONCAT(s || '=' || n, ',') FROM "
        "(SELECT status AS s, COUNT(*) AS n FROM runs GROUP BY status ORDER BY status)",
    )
    return f"{head}:{groups}" if head else ""


def _notices_fp(deps: WebDeps) -> str:
    """보낸 알림 최대 id."""
    return _q(deps, "SELECT COALESCE(MAX(id), 0) FROM notices")


# 커널 소유 SSE 키 → probe. `daemon` 은 하트비트 파일이라 따로 둔다(잦게 바뀌는 유일한 커널 키)
KERNEL_PROBES: dict[str, Callable[[WebDeps], object]] = {
    "usage": _usage_fp,
    "control": _control_fp,
    "jobs": _jobs_fp,
    "runs": _runs_fp,
    "notices": _notices_fp,
}
KERNEL_SSE_KEYS = ("daemon", *KERNEL_PROBES)
KERNEL_NOISY_KEYS = ("daemon",)


def _probe(snap: dict[str, Any], key: str, probe: Callable[[WebDeps], object], deps) -> None:
    try:
        snap[key] = probe(deps)
    except Exception:
        logger.warning("SSE probe 실패: %s", key, exc_info=True)
        snap[key] = ""


def snapshot(deps: WebDeps) -> dict:
    snap: dict[str, Any] = {"daemon": _daemon_fp(deps)}
    for key, probe in KERNEL_PROBES.items():
        _probe(snap, key, probe, deps)
    for feature in deps.features:
        for key in feature.sse_keys:
            _probe(snap, key.key, key.probe, deps)
    return snap


def _shared_snapshot(deps: WebDeps, share_s: float) -> dict:
    """연결이 여럿이어도 `share_s` 안에서는 스냅샷을 한 번만 계산해 나눠 쓴다."""
    with _SHARED_LOCK:
        if _SHARED["deps"] is deps and time.monotonic() - _SHARED["at"] < share_s:
            return _SHARED["snap"]
        snap = snapshot(deps)
        _SHARED.update(deps=deps, at=time.monotonic(), snap=snap)
        return snap


async def stream(
    deps: WebDeps,
    *,
    poll_s: float = 1.5,
    share_s: float = 1.5,
    ping_s: float = 15,
    once: bool = False,
) -> AsyncIterator[str]:
    last: dict | None = None
    last_sent = time.monotonic()
    while True:
        snap = await run_in_threadpool(_shared_snapshot, deps, share_s)
        if snap != last:
            last = snap
            last_sent = time.monotonic()
            yield f"event: change\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
            if once:
                return
        elif time.monotonic() - last_sent >= ping_s:
            last_sent = time.monotonic()
            stamp = json.dumps({"server_time": deps.now().isoformat()})
            yield f"event: ping\ndata: {stamp}\n\n"
        await anyio.sleep(poll_s)


@router.get("/events")
async def events(request: Request, once: int = 0) -> StreamingResponse:
    return StreamingResponse(
        stream(request.app.state.deps, once=bool(once)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
