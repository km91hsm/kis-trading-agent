"""spot 웹 자원. 조회는 토큰 없음, 결정은 토큰 + 같은 출처.

`approvals_router`: `GET /approvals?status=pending|all`, `PATCH /approvals/{id} {action, confirm?}`.
웹 프로세스는 주문을 내지 않는다 — 승인은 `approvals.decide` 선점 + `approve_execute` 큐
요청(202)까지, 집행은 데몬 큐 틱(요청을 못 넣거나 대기 중에 취소되면 선점을 푼다). 거부는 원장
결정 뒤 `approval_notice` 큐 요청 — 텔레그램 알림은 데몬이 보낸다. 데몬이 꺼져 있으면 승인을
받지 않는다(선점이 집행 없이 남지 않게). 계좌는 `deps.ports["accounts"]`(없으면 계좌 없음 →
모든 승인이 실전 확인 필요). 승인 행은 주문 시장·통화(`market`·`currency`)를 싣는다.

`positions_router`(조회만): `GET /positions/overview?market=`(현물 전체 합산),
`GET /positions/risk?account_id=&market=`(위험 카드). `RESOURCES`: `/accounts/{id}/positions`
현물 조각(`positions_view.spot_rows`), `/decisions`·`/orders` 현물(단타 포함) 조각.

`decisions_router`(조회만, `decisions_view`): `GET /decisions/spot/news-refs?date=&market=`,
`GET /decisions/spot/{id}`(+`/trace`·`/outcomes`·`/checkpoints/{stage}`),
`GET /markets/{market}/today?date=&stage=`(stage 가 있으면 시장 단계 파일 하나),
`GET /risk-checks?date=&account_id=&market=`. SSE `decisions`·`orders`·`triggers`."""

from __future__ import annotations

import logging
from contextlib import closing
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from trader.features.spot import approvals, decisions_view, positions_view, risk_view
from trader.features.spot.excluded import excluded_tickers
from trader.features.spot.executor import sector_of_for
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.catalog import with_kernel
from trader.kernel.registry.types import ResourcePart, SseKey
from trader.kernel.session.run_store import run_date_for
from trader.kernel.web.auth import actor_of, require_same_origin, require_token
from trader.kernel.web.routers import jobs as jobs_router

WRITE = [Depends(require_token), Depends(require_same_origin)]
logger = logging.getLogger("trader.spot")

approvals_router = APIRouter(tags=["approvals"])
positions_router = APIRouter(tags=["positions"])
decisions_router = APIRouter(tags=["decisions"])


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
        kinds = jobq.kinds_of(with_kernel(deps.features))
        if body.action == "reject":
            _notice_reject(conn, kinds, approval_id, actor, deps.now())
            return d
        try:
            rid = jobq.enqueue(
                conn, kinds, "approve_execute", {"approval_id": approval_id, "actor": actor},
                source="web", now=deps.now(),
            )  # fmt: skip
        except jobq.DuplicateRequest as e:  # 집행 요청을 못 넣었다 — 이 결정의 선점은 푼다
            approvals.release(conn, approval_id)
            raise HTTPException(
                status_code=409, detail={"ok": False, "status": "duplicate", "error": str(e)}
            ) from e
        except BaseException:  # 요청을 못 넣었다 — 집행 없는 선점이 남지 않게 푼다
            approvals.release(conn, approval_id)
            raise
    response.status_code = 202
    return {"status": "accepted", "request_id": rid}


def _notice_reject(conn: Any, kinds: jobq.Kinds, approval_id: int, actor: str | None,
                   now: Any) -> None:  # fmt: skip
    """거부 알림 요청(`approval_notice`)을 큐에 넣는다 — 보내기는 데몬(웹은 알림을 쥐지 않는다).
    못 넣어도 이미 확정된 거부를 뒤집지 않는다(로그만)."""
    params = {"approval_id": approval_id, "action": "reject", "actor": actor, "channel": "web"}
    try:
        jobq.enqueue(conn, kinds, "approval_notice", params, source="web", now=now)
    except Exception as e:  # noqa: BLE001 - 알림 요청 실패가 거부 결과를 바꾸지 않는다
        logger.warning("거부 알림 요청 실패 #%s: %s", approval_id, e)


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
    """위험 카드: VaR·CVaR 95%·베타·상관·위험 기여 + 설정 한도(`limits`)·사용량(`usage`, 섹터는
    `ports["scan"]`, 헤지·현금 운용 ETF 는 `excluded_tickers` 로 뺀다). 종가는 ml 포트, 표시
    전용. 현물 계좌만(그 외 400), market 생략은 계좌 첫 시장."""
    deps = request.app.state.deps
    acct = next((a for a in _accounts(deps) if a.id == account_id), None)
    if acct is None:
        raise HTTPException(status_code=400, detail=f"알 수 없는 현물 계좌: {account_id}")
    m = _market(market) or acct.markets[0]
    if m not in acct.markets:
        raise HTTPException(status_code=400, detail=f"계좌 {acct.id} 에 없는 시장: {m}")
    with closing(deps.conn_factory()) as conn:
        card = risk_view.risk_card(
            conn, (deps.ports or {}).get("ml"), account_id=acct.id, market=m,
            today=run_date_for(m, deps.now()), limits=deps.settings.risk,
            sector_of=sector_of_for(deps.ports, m),
            exclude=excluded_tickers(deps.settings, (deps.ports or {}).get("regime"), m),
        )  # fmt: skip
    return {"account_id": acct.id, "market": m, **card}


def _days(deps: Any, date_: str | None, market: str | None) -> dict:
    try:
        return decisions_view.days_by_market({"date": date_ or "", "market": _market(market) or ""},
                                             deps.now())  # fmt: skip
    except ValueError:
        raise HTTPException(status_code=400, detail=f"date 형식 오류: {date_!r}") from None


def _decision_or_404(conn: Any, decision_id: int) -> dict:
    dec = decisions_view.get_decision(conn, decision_id)
    if dec is None:
        raise HTTPException(status_code=404, detail=f"결정 없음: {decision_id}")
    return dec


@decisions_router.get("/decisions/spot/news-refs")
def news_refs(
    request: Request, date_: str | None = Query(None, alias="date"), market: str | None = None
) -> dict:
    """그 거래일 결정들이 근거로 쓴 뉴스 `{"<news_id>": [decision_id, …]}`(뉴스 목록 라벨용)."""
    deps = request.app.state.deps
    days = _days(deps, date_, market)
    with closing(deps.conn_factory()) as conn:
        return decisions_view.news_refs_map(conn, days)


@decisions_router.get("/decisions/spot/{decision_id}")
def decision_detail(request: Request, decision_id: int) -> dict:
    """결정 + 라운드 + 요약 + 쓴 뉴스 id(`news_refs`)."""
    with closing(request.app.state.deps.conn_factory()) as conn:
        body = decisions_view.detail(conn, decision_id)
    if body is None:
        raise HTTPException(status_code=404, detail=f"결정 없음: {decision_id}")
    return body


@decisions_router.get("/decisions/spot/{decision_id}/trace")
def decision_trace(request: Request, decision_id: int) -> dict:
    """판단 과정 한 묶음: 애널리스트 → 토론 → 결정 → 게이트 → 주문 + 체크포인트 목록."""
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        tr = decisions_view.trace(conn, decision_id, deps.state)
    if tr is None:
        raise HTTPException(status_code=404, detail=f"결정 없음: {decision_id}")
    return tr


@decisions_router.get("/decisions/spot/{decision_id}/outcomes")
def decision_outcomes(request: Request, decision_id: int) -> dict:
    with closing(request.app.state.deps.conn_factory()) as conn:
        dec = _decision_or_404(conn, decision_id)
        return {"decision": dec, "outcomes": decisions_view.outcomes(conn, decision_id)}


@decisions_router.get("/decisions/spot/{decision_id}/checkpoints/{stage}")
def decision_checkpoint(request: Request, decision_id: int, stage: str) -> dict:
    """결정 종목 run 디렉터리의 체크포인트 하나(조회는 디렉터리를 만들지 않는다)."""
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        dec = _decision_or_404(conn, decision_id)
    try:
        cp = decisions_view.checkpoint(dec, stage, deps.state)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if cp is None:
        raise HTTPException(status_code=404, detail=f"체크포인트 없음: {stage}")
    return cp


@decisions_router.get("/markets/{market}/today")
def market_today(
    request: Request, market: str, date_: str | None = Query(None, alias="date"),
    stage: str | None = None,
) -> dict:  # fmt: skip
    """그 운영일(기본 시장 오늘)의 종목별 단계·스캔 요약·시장 단계 파일·뉴스 요청. `stage` 를 주면
    시장 단계 파일 하나(`_scan`·`_market_view` …)."""
    deps = request.app.state.deps
    m = _market(market)
    (d,) = _days(deps, date_, m).values()
    if stage is not None:
        try:
            cp = decisions_view.market_checkpoint(m, d, stage, deps.state)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        if cp is None:
            raise HTTPException(status_code=404, detail=f"시장 단계 파일 없음: {stage}")
        return cp
    with closing(deps.conn_factory()) as conn:
        return decisions_view.market_today(conn, m, d, deps.state)


@decisions_router.get("/risk-checks")
def risk_checks(
    request: Request, date_: str | None = Query(None, alias="date"),
    account_id: str | None = None, market: str | None = None,
) -> list[dict]:  # fmt: skip
    """그 거래일(기본 시장마다 오늘) 결정들의 계좌별 판정."""
    deps = request.app.state.deps
    days = _days(deps, date_, market)
    with closing(deps.conn_factory()) as conn:
        return decisions_view.risk_checks(conn, days, account_id or None)


ROUTERS = (approvals_router, positions_router, decisions_router)
RESOURCES = (
    ResourcePart("/accounts/{account_id}/positions", positions_view.spot_rows),
    ResourcePart("/decisions", decisions_view.decision_rows),
    ResourcePart("/orders", decisions_view.order_rows),
)
SSE_KEYS = (
    SseKey("decisions", decisions_view.decisions_fp),
    SseKey("orders", decisions_view.orders_fp),
    SseKey("triggers", decisions_view.triggers_fp),
)
