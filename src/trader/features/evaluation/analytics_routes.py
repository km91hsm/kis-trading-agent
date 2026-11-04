"""evaluation 웹 「성과」 자원(토큰 없음·읽기 전용). 모양은 `analytics_view`, 여기는 검증만.

`analytics_router`(prefix `/analytics`): `GET /performance?account_id=&market=&days=`(옛
`/performance`, 캐시)·`/performance/compare?days=`(계좌·시장별 `metrics_of`)·`/shadow?market=`·
`/calibration?limit=`·`/memory?q=&limit=`·`/rl/experiments`·`/rl/experiments/{version}`·
`/rl/shadow?account_id=&days=`·`/rl/report?account_id=&market=&weeks=`(옛 `rl.py`).
계좌는 `deps.ports["accounts"]`(현물 + 파생, 모르면 400). SSE `market_views`·`shadow`·`memory`
(옛 `sse.py` 지문 그대로)."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from trader.features.evaluation import analytics_view
from trader.features.evaluation.rl import report as rl_report
from trader.features.evaluation.rl import shadow as rl_shadow
from trader.features.ml import api as ml_api
from trader.kernel.registry.types import SseKey

analytics_router = APIRouter(prefix="/analytics", tags=["analytics"])

MARKETS = ("KR", "US")
_VERSION_RE = re.compile(r"^\d{8}_\d{6}$")


def _all_accounts(deps: Any) -> list:
    port = (deps.ports or {}).get("accounts")
    if port is None:
        return []
    fo = getattr(port, "fo_accounts", None)
    return [*port.accounts(), *(fo() if fo else [])]


def _account(deps: Any, account_id: str | None) -> Any:
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id 가 필요합니다")
    acct = next((a for a in _all_accounts(deps) if a.id == account_id), None)
    if acct is None:
        raise HTTPException(status_code=400, detail=f"알 수 없는 계좌: {account_id}")
    return acct


def _market(market: str | None, acct: Any) -> str:
    if market not in MARKETS:
        raise HTTPException(status_code=400, detail="market 은 KR 또는 US 여야 합니다")
    if market not in acct.markets:
        raise HTTPException(
            status_code=400, detail=f"계좌 {acct.id} 는 {market} 시장을 쓰지 않습니다"
        )
    return market


@analytics_router.get("/performance")
def performance_report(
    request: Request,
    account_id: str | None = None,
    market: str | None = None,
    days: int = Query(30, ge=1, le=3650),
) -> dict:
    """계좌·시장 성과 리포트(NAV·지표·벤치마크·비용 민감도·회전율). 입력이 같으면 캐시."""
    deps = request.app.state.deps
    acct = _account(deps, account_id)
    m = _market(market, acct)
    with closing(deps.conn_factory()) as conn:
        return analytics_view.cached_report(
            conn, deps.state, acct.id, m, days, asset=getattr(acct, "asset", "stock")
        )


@analytics_router.get("/performance/compare")
def performance_compare(request: Request, days: int = Query(30, ge=1, le=3650)) -> dict:
    """계좌 비교: 계좌·시장마다 수익률·MDD·Sortino(NAV 이력 없으면 metrics null)."""
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        rows = analytics_view.compare_rows(conn, _all_accounts(deps), days=days)
    return {"days": days, "rows": rows}


@analytics_router.get("/shadow")
def shadow_report(request: Request, market: str | None = None) -> dict:
    """섀도 포트폴리오(시장 단위, 계좌 무관). 모르는 시장은 KR."""
    m = (market or "KR").upper()
    if m not in MARKETS:
        m = "KR"
    with closing(request.app.state.deps.conn_factory()) as conn:
        return analytics_view.shadow_summary(conn, m)


@analytics_router.get("/calibration")
def calibration(request: Request, limit: int = Query(20, ge=1, le=200)) -> dict:
    """보정 실행 기록(`calibration_runs`) 최근."""
    with closing(request.app.state.deps.conn_factory()) as conn:
        return {"runs": analytics_view.calibration_runs(conn, limit=limit)}


@analytics_router.get("/memory")
def memory(request: Request, q: str | None = None, limit: int = Query(20, ge=1, le=200)) -> dict:
    """복기: q(종목) 가 있으면 그 상황으로 메모리 검색, 없으면 최근 엔트리."""
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        if q and q.strip():
            return analytics_view.memory_search(
                conn, q, limit=limit, now=deps.now(), settings=deps.settings.memory
            )
        return analytics_view.memory_recent(conn, limit=limit)


@analytics_router.get("/rl/experiments")
def rl_experiments(request: Request) -> dict:
    metas = ml_api.registry_list(rl_report.NAME, request.app.state.deps.state)
    active = next((m for m in metas if m.get("promoted")), None)
    return {
        "experiments": [rl_report.model_summary(m) for m in metas],
        "active": rl_report.model_summary(active) if active else None,
    }


@analytics_router.get("/rl/experiments/{version}")
def rl_experiment_detail(request: Request, version: str) -> dict:
    if not _VERSION_RE.match(version):
        raise HTTPException(status_code=400, detail="버전 형식 오류")
    for m in ml_api.registry_list(rl_report.NAME, request.app.state.deps.state):
        if m.get("version") == version:
            return m
    raise HTTPException(status_code=404, detail=f"실험 없음: {version}")


@analytics_router.get("/rl/shadow")
def rl_shadow_rows(
    request: Request, account_id: str | None = None, days: int = Query(30, ge=1, le=365)
) -> dict:
    deps = request.app.state.deps
    acct = _account(deps, account_id)
    with closing(deps.conn_factory()) as conn:
        return {"rows": rl_shadow.recent(conn, acct.id, days=days)}


@analytics_router.get("/rl/report")
def rl_comparison(
    request: Request,
    account_id: str | None = None,
    market: str | None = None,
    weeks: int = Query(8, ge=1, le=52),
) -> dict:
    deps = request.app.state.deps
    acct = _account(deps, account_id)
    if market is not None:
        market = _market(market, acct)
    with closing(deps.conn_factory()) as conn:
        return rl_report.comparison(
            conn, deps.state, account_id=acct.id, market=market, weeks=weeks
        )


ROUTERS = (analytics_router,)


# --- SSE 지문 (옛 `sse.py` 의 `_q` 모양) -------------------------------------------------------


def _q(conn: sqlite3.Connection, sql: str) -> str:
    row = conn.execute(sql).fetchone()
    return ":".join("" if v is None else str(v) for v in tuple(row))


def _probe(sql: str):
    def probe(deps: Any) -> str:
        with closing(deps.conn_factory()) as conn:
            return _q(conn, sql)

    return probe


SSE_KEYS = (
    SseKey(
        "market_views",
        _probe("SELECT COUNT(*), MAX(id), COUNT(matured_at) FROM market_views"),
    ),
    SseKey("shadow", _probe("SELECT COUNT(*), COUNT(fwd_ret) FROM shadow_portfolios")),
    SseKey("memory", _probe("SELECT COUNT(*), COUNT(outcome_at) FROM memory_entries")),
)
