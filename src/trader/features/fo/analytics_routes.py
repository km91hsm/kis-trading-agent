"""fo 「성과」 웹 조회(토큰 없음·읽기 전용): `GET /analytics/fo/scorecard`·`/analytics/fo/report?
account_id=&days=`·`/analytics/fo/summary`(옛 `web/routers/fo.py` 의 세 조회).

레인이라 evaluation 을 import 하지 않는다 — 파생 NAV 는 자기 테이블 `fo_nav_snapshot` 을 직접 읽는다
(evaluation `performance.nav_series(asset="fo")` 와 같은 창: 마지막 날짜 기준 `days` 일, NAV 있는
평일만). `summary` 는 `/fo/summary`(T6-R2 파생 NAV 노출처)와 같은 응답이다."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Query, Request

from trader.features.fo.routes import _fo_account, summary

analytics_fo_router = APIRouter(prefix="/analytics/fo", tags=["analytics"])


def nav_series(
    conn: sqlite3.Connection, account_id: str, *, days: int
) -> list[tuple[str, Decimal]]:
    last = conn.execute(
        "SELECT MAX(date) AS d FROM fo_nav_snapshot WHERE account_id = ? AND nav IS NOT NULL",
        (account_id,),
    ).fetchone()["d"]
    if last is None:
        return []
    since = (date.fromisoformat(last) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT date, nav FROM fo_nav_snapshot WHERE account_id = ? AND nav IS NOT NULL "
        "AND date > ? ORDER BY date",
        (account_id, since),
    ).fetchall()
    return [
        (r["date"], Decimal(str(r["nav"])))
        for r in rows
        if date.fromisoformat(r["date"]).weekday() < 5
    ]


@analytics_fo_router.get("/scorecard")
def scorecard(request: Request) -> dict:
    """만기가 채점된 파생 결정(`fo_decisions.matured_at`, FLAT 제외)의 방향 적중률·계약 손익 합."""
    with closing(request.app.state.deps.conn_factory()) as conn:
        rows = conn.execute(
            "SELECT direction_hit, contract_pnl FROM fo_decisions WHERE matured_at IS NOT NULL "
            "AND direction != 'FLAT'"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM fo_decisions").fetchone()[0]
    hits = [r["direction_hit"] for r in rows if r["direction_hit"] is not None]
    pnl = sum((Decimal(str(r["contract_pnl"])) for r in rows if r["contract_pnl"]), Decimal(0))
    return {
        "decisions": int(total), "matured": len(rows),
        "hit_rate": (sum(hits) / len(hits)) if hits else None, "contract_pnl": str(pnl),
    }  # fmt: skip


@analytics_fo_router.get("/report")
def report(
    request: Request, account_id: str = Query(...), days: int = Query(30, ge=1, le=365)
) -> dict:
    """파생 계좌 NAV 추이 — 현물 성과와 같은 (날짜, NAV) 모양 + 기간 수익률."""
    deps = request.app.state.deps
    acct = _fo_account(deps, account_id)
    with closing(deps.conn_factory()) as conn:
        series = nav_series(conn, acct.id, days=days)
    first = series[0][1] if series else None
    last = series[-1][1] if series else None
    ret = float(last / first - 1) if first and last and first > 0 else None
    return {
        "account_id": acct.id, "days": days,
        "nav": [{"date": d, "nav": str(v)} for d, v in series], "return": ret,
    }  # fmt: skip


analytics_fo_router.add_api_route("/summary", summary, methods=["GET"])
