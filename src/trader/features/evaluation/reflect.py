"""복기 세션(스펙 §6.5): 결과가 채워졌지만 반성이 없는 엔트리를 reflector 에게 넘긴다.

세션은 만기 엔트리가 있을 때만 띄운다(LLM 비용). FAKE_CLAUDE=1 이면 NEUTRAL 반성."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime

from trader.features.evaluation.session_kinds import REFLECT, agents_for_runner
from trader.kernel.config import KST
from trader.kernel.lane.types import LaneContext
from trader.kernel.session.runner import SessionResult, SessionSpec, run_session

logger = logging.getLogger("trader.evaluation")


def pending(conn: sqlite3.Connection, *, market: str | None = None, limit: int = 20) -> list[dict]:
    """반성이 없는 만기 엔트리 + 당시 애널리스트 신호 {role: signal}."""
    sql = (
        "SELECT m.*, d.date AS run_date FROM memory_entries m "
        "LEFT JOIN decisions d ON d.id = m.decision_id "
        "WHERE m.outcome_at IS NOT NULL AND m.reflection IS NULL"
    )
    args: list = []
    if market:
        sql += " AND m.market=?"
        args.append(market)
    out = []
    for r in conn.execute(sql + " ORDER BY m.outcome_at LIMIT ?", [*args, limit]).fetchall():
        # 보고서 날짜 = 결정 행의 운영일(US 는 뉴욕 날짜), 없으면 decided_at 의 KST 날짜
        try:
            day = r["run_date"] or (
                datetime.fromisoformat(r["decided_at"]).astimezone(KST).date().isoformat()
            )
        except ValueError:
            day = None
        signals = {}
        if day:
            for a in conn.execute(
                "SELECT role, signal FROM analyst_reports "
                "WHERE decision_date=? AND market=? AND ticker=?",
                (day, r["market"], r["ticker"]),
            ).fetchall():
                signals[a["role"]] = a["signal"]
        out.append(
            {
                "entry_id": int(r["id"]),
                "ticker": r["ticker"],
                "market": r["market"],
                "decided_at": r["decided_at"],
                "decision": json.loads(r["decision"] or "{}"),
                "situation_summary": r["situation_summary"],
                "horizon_days": r["horizon_days"],
                "round": r["round"],
                "price_return": r["price_return"],
                "held_at_decision": None
                if r["held_at_decision"] is None
                else bool(r["held_at_decision"]),
                "direction_score": r["direction_score"],
                "avoided_loss": r["avoided_loss"],
                "realized_pnl_pct": r["realized_pnl_pct"],
                "post_fill_return": r["post_fill_return"],
                "analyst_signals": signals,
            }
        )
    return out


def run_reflect(
    ctx: LaneContext, *, market: str, date: date, cancel=None
) -> SessionResult:
    """대기 중 반성이 있으면 reflect 세션 1회. 없으면 세션 없이 ok 결과."""
    items = pending(ctx.conn, market=market)
    if not items:
        return SessionResult(ok=True, session_id=None, stages_written=[])
    spec = SessionSpec(
        market=market,
        date=date,
        tickers=[],
        dry_run=True,
        asof=ctx.now(),
        kind="reflect",
        extra={"entries": items},
    )
    return run_session(spec, REFLECT, ctx, agents=agents_for_runner(), cancel=cancel)
