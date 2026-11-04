"""runs·calls: 실행 기록(`runs`)과 LLM 호출(`agent_calls`) 조회. `?format=csv` 는 같은 표를 CSV 로.
옛 `web/routers/runs.py` 의 `/runs`·`/calls` 이식(필터 kind·date·run_id 추가)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Query, Request

from trader.kernel.config import KST
from trader.kernel.session import calls as calls_mod
from trader.kernel.web.csv import csv_response

router = APIRouter(tags=["runs"])


def _day_bounds(d: date) -> tuple[str, str]:
    """KST 하루 → UTC ISO [시작, 끝)."""
    start = datetime.combine(d, time(0), tzinfo=KST)
    end = start + timedelta(days=1)
    return start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()


def _select(conn: sqlite3.Connection, sql: str, args: list) -> tuple[list[dict], list[str]]:
    cur = conn.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(r) for r in cur.fetchall()], cols


@router.get("/runs")
def runs(
    request: Request,
    limit: int = Query(20, ge=1, le=200),
    kind: str | None = None,
    date: str | None = None,  # noqa: A002 - 쿼리 이름
    format: str | None = None,  # noqa: A002 - 쿼리 이름
):
    deps = request.app.state.deps
    where, args = [], []
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"date 형식 오류: {date!r}") from None
        lo, hi = _day_bounds(d)
        where.append("started_at >= ? AND started_at < ?")
        args += [lo, hi]
    sql = "SELECT * FROM runs" + (" WHERE " + " AND ".join(where) if where else "")
    with closing(deps.conn_factory()) as conn:
        rows, cols = _select(conn, sql + " ORDER BY id DESC LIMIT ?", [*args, limit])
    if format == "csv":
        return csv_response(rows, "runs.csv", cols)
    return rows


def _cost(value) -> Decimal:
    try:
        return Decimal(str(value)) if value not in (None, "") else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


@router.get("/calls")
def calls(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    run_id: int | None = None,
    format: str | None = None,  # noqa: A002 - 쿼리 이름
):
    """최근 호출(최신 순) + 오늘(KST) 세션 수·비용."""
    deps = request.app.state.deps
    now = deps.now()
    sql, args = "SELECT * FROM agent_calls", []
    if run_id is not None:
        sql += " WHERE run_id = ?"
        args.append(run_id)
    with closing(deps.conn_factory()) as conn:
        rows, cols = _select(conn, sql + " ORDER BY id DESC LIMIT ?", [*args, limit])
        if format == "csv":
            return csv_response(rows, "calls.csv", cols)
        lo, _ = _day_bounds(now.astimezone(KST).date())
        today = conn.execute(
            "SELECT cost_usd FROM agent_calls WHERE created_at >= ?", (lo,)
        ).fetchall()
        sessions = calls_mod.sessions_today(conn, now)
    cost = sum((_cost(r["cost_usd"]) for r in today), Decimal(0))
    return {"calls": rows, "sessions_today": sessions, "cost_today": float(round(cost, 4))}
