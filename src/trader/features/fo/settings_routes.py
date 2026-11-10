"""파생 계좌별 스위치 웹 API(단타 `/settings/scalp` 와 같은 모양): `GET /settings/fo`·
`PATCH /settings/fo/{account_id}` {enabled: bool|null}. 모의 파생 계좌만 켤 수 있다(실전 파생은
off 고정 → 켜기는 400). null 이면 저장값을 지워 yaml `fo.enabled` 로. 바뀌면 control 이력 `web`."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from trader.features.fo import switch
from trader.features.fo.executor import fo_mode
from trader.kernel.web.auth import actor_of, require_same_origin, require_token

WRITE = [Depends(require_token), Depends(require_same_origin)]
settings_fo_router = APIRouter(tags=["settings"])


def _fo_accounts(deps: Any) -> list[Any]:
    port = (deps.ports or {}).get("accounts")
    return [a for a in port.fo_accounts() if a.asset == "fo"] if port is not None else []


def _row(conn: sqlite3.Connection, deps: Any, a: Any) -> dict:
    enabled, source = switch.stored(conn, deps.settings, a)
    if a.env != "paper":
        effective, reason = False, "실전 파생은 항상 꺼짐"
    elif not enabled:
        effective, reason = False, "꺼짐"
    elif deps.ports["accounts"].paused(conn):
        effective, reason = False, "전체 일시정지"
    else:
        mode, note = fo_mode(conn, a, deps.ports)
        effective, reason = (True, None) if mode == "auto" else (False, note or f"모드 {mode}")
    return {
        "id": a.id, "env": a.env, "enabled": enabled, "source": source,
        "effective": effective, "reason": reason,
    }  # fmt: skip


def _view(deps: Any) -> dict:
    cfg = deps.settings.fo
    with closing(deps.conn_factory()) as conn:
        rows = [_row(conn, deps, a) for a in _fo_accounts(deps)]
    return {
        "accounts": rows, "default_paper": cfg.enabled, "product": cfg.product,
        "vehicles": list(cfg.vehicles), "max_contracts": cfg.max_contracts,
    }  # fmt: skip


class FoSwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool | None  # None 이면 저장값을 지워 기본값(yaml)으로


@settings_fo_router.get("/settings/fo")
def get_fo_settings(request: Request) -> dict:
    return _view(request.app.state.deps)


@settings_fo_router.patch("/settings/fo/{account_id}", dependencies=WRITE)
def patch_fo_switch(request: Request, account_id: str, body: FoSwitchBody) -> dict:
    """계좌별 파생 켜기·끄기·기본값 복귀(모의 전용, 다음 틱·세션부터 적용)."""
    deps = request.app.state.deps
    acct = next((a for a in _fo_accounts(deps) if a.id == account_id), None)
    if acct is None:
        raise HTTPException(status_code=404, detail=f"없는 파생 계좌: {account_id}")
    if acct.env != "paper" and body.enabled:
        raise HTTPException(status_code=400, detail="실전 파생 계좌는 켤 수 없습니다(항상 꺼짐)")
    with closing(deps.conn_factory()) as conn:
        switch.set_switch(conn, deps.settings, acct, body.enabled, channel="web",
                          actor=actor_of(request), at=deps.now().isoformat())  # fmt: skip
    return _view(deps)
