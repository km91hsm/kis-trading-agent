"""saveticker 「시스템」 웹: `GET /system/saveticker`(옛 `system.saveticker_status`, 토큰 없음)·
`POST /system/saveticker/restart`(토큰 + 같은 출처, 옛 `control.saveticker_restart`).

웹 프로세스는 수집 프로세스·수집기 파일을 건드리지 않는다(T11-R2). 재시작은 control 플래그
`RESTART_FLAG = {"at", "actor"}` 를 세우고 `control_log` 에 남길 뿐이고, 데몬 saveticker 잡
(`job.watch` → `consume_restart`)이 다음 틱(≤1분)에 소비한다. 계정·값은 돌려주지 않는다."""

from __future__ import annotations

import logging
from contextlib import closing
from datetime import UTC

from fastapi import APIRouter, Depends, Request

from trader.features.saveticker import automation
from trader.features.saveticker.paths import credentials_path, home
from trader.features.saveticker.restart import EXTENSION_NOTE, RESTART_FLAG
from trader.features.saveticker.web_status import read_status
from trader.kernel.ledger import control
from trader.kernel.web.auth import actor_of, require_same_origin, require_token

logger = logging.getLogger("trader.saveticker")

__all__ = ["EXTENSION_NOTE", "RESTART_FLAG", "ROUTERS"]

WRITE = [Depends(require_token), Depends(require_same_origin)]

system_saveticker_router = APIRouter(prefix="/system", tags=["system"])


@system_saveticker_router.get("/saveticker")
def saveticker_status(request: Request) -> dict:
    """수집기 상태: 계정 파일 유무(확장 모드는 계정 불필요)·서비스 상태·멈춘 이유·작업별 상태·
    쉬는 작업·마지막 수집 시각."""
    state = read_status(home(), now=request.app.state.deps.now())
    return {
        "configured": credentials_path().is_file() or state.get("backend") == "extension",
        **state,
    }


@system_saveticker_router.post("/saveticker/restart", dependencies=WRITE)
def saveticker_restart(request: Request) -> dict:
    """'다시 시작' 요청을 남긴다(데몬 잡이 소비). 확장 모드면 화면 안내(`note`)를 붙인다."""
    deps = request.app.state.deps
    actor = actor_of(request)
    at = deps.now().astimezone(UTC).isoformat()
    value = {"at": at, "actor": actor}
    with closing(deps.conn_factory()) as conn:
        old = control.get_flag(conn, RESTART_FLAG)
        control.set_flag(conn, RESTART_FLAG, value)
        control.log_change(
            conn, channel="web", actor=actor, key=RESTART_FLAG, old=old, new=value, at=at
        )
    logger.info("세이브티커 다시 시작 요청(웹, %s)", actor)
    backend = (automation.read(home() / "runtime" / "bridge-config.json") or {}).get("backend")
    return {
        "ok": True,
        "requested": value,
        "note": EXTENSION_NOTE if backend == "extension" else None,
    }


ROUTERS = (system_saveticker_router,)
