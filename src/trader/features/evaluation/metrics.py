"""원장 집계 (스펙 §6.3): 결정 분포·수렴율. (미승인 주문·계좌 성향은 M11 웹)"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from trader.kernel.config import KST
from trader.kernel.session.debate import CONVERGENCE_GAP

_SIGN = {"BULLISH": 1.0, "NEUTRAL": 0.0, "BEARISH": -1.0}


def _since(days: int, now: datetime | None) -> str:
    """decisions.date 는 KST 날짜이므로 기준일도 KST 로 잡는다."""
    now = now or datetime.now(UTC)
    return (now.astimezone(KST).date() - timedelta(days=days)).isoformat()


def decision_distribution(
    conn: sqlite3.Connection,
    *,
    days: int,
    now: datetime | None,
    market: str | None = None,
) -> dict[str, int]:
    """최근 `days` 일 결정의 action 별 건수 (0건인 action 은 생략)."""
    sql = "SELECT action, COUNT(*) AS n FROM decisions WHERE date >= ? AND source='llm'"
    args: list = [_since(days, now)]
    if market:
        sql += " AND market = ?"
        args.append(market)
    rows = conn.execute(sql + " GROUP BY action ORDER BY action", args).fetchall()
    return {r["action"]: int(r["n"]) for r in rows}


CONVERGENCE_DEFINITION = "판(강세·약세 한 쌍) 단위 — 4인 교차 토론 결정은 표본 2개"


def convergence_rate(conn: sqlite3.Connection, *, days: int, now: datetime | None) -> float | None:
    """최종 라운드에서 양측 부호 확신 차이가 CONVERGENCE_GAP 이하인 판 비율 (토론 없으면 None)."""
    rows = conn.execute(
        "SELECT decision_date, market, ticker, lane, side, round, stance, conviction "
        "FROM debate_turns WHERE decision_date >= ?",
        (_since(days, now),),
    ).fetchall()
    by_key: dict[tuple, dict[int, dict[str, float]]] = {}
    for r in rows:
        signed = float(r["conviction"] or 0.0) * _SIGN.get(r["stance"] or "", 0.0)
        by_key.setdefault((r["decision_date"], r["market"], r["ticker"], r["lane"]), {}).setdefault(
            int(r["round"]), {}
        )[r["side"]] = signed
    total = converged = 0
    for rounds in by_key.values():
        final = rounds[max(rounds)]
        if "bull" not in final or "bear" not in final:
            continue
        total += 1
        if abs(final["bull"] - final["bear"]) <= CONVERGENCE_GAP:
            converged += 1
    return converged / total if total else None
