"""SSE `/api/events`: 지문 스냅샷이 바뀔 때만 `change`, 주기적으로 서버 시각을 담은 ping.

스냅샷 = 커널 키 `daemon` + 모든 `feature.sse_keys` 의 `probe(deps)`. 비동기 제너레이터라
스레드 풀을 점유하지 않는다(스냅샷 계산만 run_in_threadpool).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import AsyncIterator
from typing import Any

import anyio
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from trader.kernel.daemon.heartbeat import read_status
from trader.kernel.web.deps import WebDeps

logger = logging.getLogger("trader.web")
router = APIRouter(tags=["events"])

KERNEL_SSE_KEYS = ("daemon",)

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


def snapshot(deps: WebDeps) -> dict:
    snap: dict[str, Any] = {"daemon": _daemon_fp(deps)}
    for feature in deps.features:
        for key in feature.sse_keys:
            try:
                snap[key.key] = key.probe(deps)
            except Exception:
                logger.warning("SSE probe 실패: %s", key.key, exc_info=True)
                snap[key.key] = ""
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
            yield f": ping {stamp}\n\n"
        await anyio.sleep(poll_s)


@router.get("/events")
async def events(request: Request, once: int = 0) -> StreamingResponse:
    return StreamingResponse(
        stream(request.app.state.deps, once=bool(once)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
