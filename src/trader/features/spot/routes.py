"""spot 웹 자원. 조회는 토큰 없음, 결정은 토큰 + 같은 출처.

`approvals_router`: `GET /approvals?status=pending|all`, `PATCH /approvals/{id} {action, confirm?}`.
웹 프로세스는 주문을 내지 않는다 — 승인은 `approvals.decide` 선점 + `approve_execute` 큐
요청(202)까지, 집행은 데몬 큐 틱. 데몬이 꺼져 있으면 승인을 받지 않는다(선점이 집행 없이 남지
않게). 계좌는 `deps.ports["accounts"]`(없으면 계좌 없음 → 모든 승인이 실전 확인 필요).

`positions_router`(조회만): `GET /positions/overview?market=`(현물 전체 합산),
`GET /positions/risk?account_id=&market=`(위험 카드). `RESOURCES`: `/accounts/{id}/positions`
현물 조각(`positions_view.spot_rows`)."""

from __future__ import annotations

from contextlib import closing
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from trader.features.spot import approvals, positions_view
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.catalog import with_kernel
from trader.kernel.registry.types import ResourcePart
from trader.kernel.session.run_store import run_date_for
from trader.kernel.web.auth import actor_of, require_same_origin, require_token
from trader.kernel.web.routers import jobs as jobs_router

WRITE = [Depends(require_token), Depends(require_same_origin)]

approvals_router = APIRouter(tags=["approvals"])
positions_router = APIRouter(tags=["positions"])


class DecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]
    confirm: str | None = None


def _accounts(deps: Any) -> list:
    port = (deps.ports or {}).get("accounts")
    return list(port.accounts()) if port is not None else []


@approvals_router.get("/approvals")
def list_approvals(request: Request, status: Literal["pending", "all"] = "pending") -> dict:
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        return {"approvals": approvals.list_rows(conn, _accounts(deps), status=status)}


@approvals_router.patch("/approvals/{approval_id}", dependencies=WRITE)
def decide_approval(
    request: Request, approval_id: int, body: DecideBody, response: Response
) -> dict:
    deps = request.app.state.deps
    if body.action == "approve" and not jobs_router.daemon_alive(deps):
        raise HTTPException(status_code=409, detail=jobs_router.DAEMON_DOWN)
    actor = actor_of(request)
    with closing(deps.conn_factory()) as conn:
        d = approvals.decide(
            conn, approval_id, body.action, channel="web", actor=actor, confirm=body.confirm,
            now=deps.now(), accounts=_accounts(deps),
        )  # fmt: skip
        if not d["ok"]:
            code = 400 if d["status"] == "confirm_required" else 409
            raise HTTPException(status_code=code, detail=d)
        if body.action == "reject":
            return d
        kinds = jobq.kinds_of(with_kernel(deps.features))
        try:
            rid = jobq.enqueue(
                conn, kinds, "approve_execute", {"approval_id": approval_id, "actor": actor},
                source="web", now=deps.now(),
            )  # fmt: skip
        except jobq.DuplicateRequest as e:  # 같은 집행이 이미 대기·실행 중 — 선점은 그쪽 몫
            raise HTTPException(
                status_code=409, detail={"ok": False, "status": "duplicate", "error": str(e)}
            ) from e
        except BaseException:  # 요청을 못 넣었다 — 집행 없는 선점이 남지 않게 푼다
            approvals.release(conn, approval_id)
            raise
    response.status_code = 202
    return {"status": "accepted", "request_id": rid}


def _market(value: str | None) -> str | None:
    m = (value or "").upper() or None
    if m is not None and m not in positions_view.MARKETS:
        raise HTTPException(status_code=400, detail="market 은 KR|US")
    return m


@positions_router.get("/positions/overview")
def positions_overview(request: Request, market: str | None = None) -> dict:
    """현물 계좌 전체 합산(시장·환경별, 파생 제외). `market` 을 주면 그 시장만."""
    deps = request.app.state.deps
    m = _market(market)
    with closing(deps.conn_factory()) as conn:
        return positions_view.overview(conn, deps, [m] if m else None)


@positions_router.get("/positions/risk")
def positions_risk(
    request: Request, account_id: str = Query(...), market: str | None = None
) -> dict:
    """위험 카드: VaR·CVaR 95%·베타·상관·위험 기여. 종가는 ml 포트, 표시 전용. 현물 계좌만(그 외
    400), market 생략은 계좌 첫 시장."""
    deps = request.app.state.deps
    acct = next((a for a in _accounts(deps) if a.id == account_id), None)
    if acct is None:
        raise HTTPException(status_code=400, detail=f"알 수 없는 현물 계좌: {account_id}")
    m = _market(market) or acct.markets[0]
    if m not in acct.markets:
        raise HTTPException(status_code=400, detail=f"계좌 {acct.id} 에 없는 시장: {m}")
    with closing(deps.conn_factory()) as conn:
        card = positions_view.risk_card(
            conn, (deps.ports or {}).get("ml"), account_id=acct.id, market=m,
            today=run_date_for(m, deps.now()),
        )  # fmt: skip
    return {"account_id": acct.id, "market": m, **card}


ROUTERS = (approvals_router, positions_router)
RESOURCES = (ResourcePart("/accounts/{account_id}/positions", positions_view.spot_rows),)
