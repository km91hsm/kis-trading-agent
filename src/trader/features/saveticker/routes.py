"""saveticker 「시스템」 웹: `GET /system/saveticker`(옛 `system.saveticker_status`, 토큰 없음)·
`POST /system/saveticker/restart`(토큰 + 같은 출처, 옛 `control.saveticker_restart`)·
`POST /system/saveticker/install`(토큰 + 같은 출처 + 이 PC 직접 접속 — 확장 복사·Native host 등록)·
`POST /system/saveticker/onboarding`(첫 안내 「안 씀」).

웹 프로세스는 수집 프로세스·수집기 파일을 건드리지 않는다(T11-R2). 재시작은 control 플래그
`RESTART_FLAG = {"at", "actor"}` 를 세우고 `control_log` 에 남길 뿐이고, 데몬 saveticker 잡
(`job.watch` → `consume_restart`)이 다음 틱(≤1분)에 소비한다. 계정·값은 돌려주지 않는다.

예외: 확장 설치는 연결 키 설정처럼 이 PC 에서 직접 연 화면에서만 웹이
`bridge_install.install()` 을 바로 부른다(결과를 즉시 보여 주려고). 확장 경로는 직접
접속일 때만 응답에 싣는다."""

from __future__ import annotations

import logging
from contextlib import closing
from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from trader.features.saveticker import automation
from trader.features.saveticker.paths import home
from trader.features.saveticker.restart import EXTENSION_NOTE, RESTART_FLAG
from trader.features.saveticker.web_status import read_status
from trader.kernel.ledger import control
from trader.kernel.web.auth import (
    actor_of,
    is_direct_local,
    require_direct_local,
    require_same_origin,
    require_token,
)

logger = logging.getLogger("trader.saveticker")

__all__ = ["EXTENSION_NOTE", "ONBOARDING_FLAG", "RESTART_FLAG", "ROUTERS"]

WRITE = [Depends(require_token), Depends(require_same_origin)]
LOCAL_WRITE = [*WRITE, Depends(require_direct_local)]
ONBOARDING_FLAG = "saveticker.onboarding_dismissed"

_DAMAGED = "설치 파일이 손상됐습니다. 앱을 다시 설치한 뒤 해 보세요"
INSTALL_ERRORS = {
    "app_translocated": "앱을 응용 프로그램 폴더로 옮긴 뒤 실행하고 다시 하세요",
    "registration_exists": "다른 프로그램이 같은 이름으로 Chrome 연결을 등록해 두었습니다",
    "registration_failed": "Chrome 연결 등록에 실패했습니다. 잠시 뒤 다시 해 보세요",
    "extension_resources_missing": _DAMAGED,
    "invalid_extension_manifest": _DAMAGED,
    "windows_source_requires_bundle": "설치판(setup.exe)으로 설치한 앱에서만 할 수 있습니다",
    "install_busy": "다른 설치가 진행 중입니다. 잠시 뒤 다시 해 보세요",
}


class OnboardingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dismissed: bool


def _install_view(request: Request) -> dict:
    """확장 설치 상태(경로는 이 PC 직접 접속일 때만). 상태를 못 읽으면 미등록으로 본다."""
    from pathlib import Path

    from trader.features.saveticker import bridge_install

    try:
        st = bridge_install.install_status()
    except Exception:  # noqa: BLE001 — 상태 화면은 설치 상태 오류로 깨지지 않는다
        logger.warning("세이브티커 확장 설치 상태를 읽지 못함", exc_info=True)
        st = {}
    local = is_direct_local(request)
    package = st.get("package") if local else None
    return {
        "registered": bool(st.get("registered")),
        "installed_version": st.get("installed_version"),
        "bundled_version": st.get("bundled_version"),
        "update_needed": bool(st.get("update_needed")),
        "package": package,
        "extension_dir": str(Path(package) / "extension") if package else None,
    }


system_saveticker_router = APIRouter(prefix="/system", tags=["system"])


@system_saveticker_router.get("/saveticker")
def saveticker_status(request: Request) -> dict:
    """수집기 상태: 확장 등록 여부(`configured`)·Native host 연결 상태·멈춘 이유·작업별 상태·
    마지막 수집 시각."""
    root = home()
    deps = request.app.state.deps
    state = read_status(root, now=deps.now())
    with closing(deps.conn_factory()) as conn:
        dismissed = control.get_flag(conn, ONBOARDING_FLAG) is not None
    return {
        "configured": automation.registered(root),
        **state,
        "install": _install_view(request),
        "onboarding": {"dismissed": dismissed},
    }


@system_saveticker_router.post("/saveticker/restart", dependencies=WRITE)
def saveticker_restart(request: Request) -> dict:
    """'다시 시작' 요청을 남긴다(데몬 잡이 소비). 재개는 Chrome 확장 팝업에서 하므로 화면
    안내(`note`)를 붙인다."""
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
    return {"ok": True, "requested": value, "note": EXTENSION_NOTE}


@system_saveticker_router.post("/saveticker/install", dependencies=LOCAL_WRITE)
def saveticker_install(request: Request) -> dict:
    """확장을 데이터 폴더로 복사하고 설치된 앱을 Chrome Native host 로 등록한다(재실행 가능 —
    CLI `extension install` 과 같다). Chrome 의 「압축해제된 확장 로드」는 사용자가 한다."""
    from trader.features.saveticker import bridge_install

    try:
        result = bridge_install.install()
    except bridge_install.InstallError as exc:
        code = str(exc)
        logger.warning("세이브티커 확장 웹 설치 실패: %s", code)
        raise HTTPException(
            status_code=400, detail=INSTALL_ERRORS.get(code, "확장 설치에 실패했습니다")
        ) from None
    except OSError:
        logger.warning("세이브티커 확장 웹 설치 파일 오류", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail="확장 파일을 쓰지 못했습니다. 폴더 권한·디스크 공간을 확인한 뒤 다시 해 보세요",
        ) from None
    logger.info("세이브티커 확장 웹 설치(%s)", actor_of(request))
    return {
        "ok": True,
        "extension": str(result["extension_dir"]),
        "next_steps": list(result.get("next_steps") or []),
        "replaced": bool(result.get("replaced")),
    }


@system_saveticker_router.post("/saveticker/onboarding", dependencies=WRITE)
def saveticker_onboarding(request: Request, body: OnboardingBody) -> dict:
    """첫 안내 「안 씀」: 다시 묻지 않도록 control 플래그를 남긴다(false 면 지운다)."""
    deps = request.app.state.deps
    actor = actor_of(request)
    at = deps.now().astimezone(UTC).isoformat()
    value = {"at": at, "actor": actor} if body.dismissed else None
    with closing(deps.conn_factory()) as conn:
        old = control.get_flag(conn, ONBOARDING_FLAG)
        if value is None:
            control.delete_flag(conn, ONBOARDING_FLAG)
        else:
            control.set_flag(conn, ONBOARDING_FLAG, value)
        control.log_change(
            conn, channel="web", actor=actor, key=ONBOARDING_FLAG, old=old, new=value, at=at
        )
    return {"ok": True}


ROUTERS = (system_saveticker_router,)
