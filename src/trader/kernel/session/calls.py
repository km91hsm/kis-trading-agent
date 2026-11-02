"""세션 행(agent_calls): 시작(상한 검사 포함)·종료와 오늘(KST) 세션 수 집계."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from trader.kernel.config.time import KST
from trader.kernel.ledger import db

log = logging.getLogger("trader.session")

FAKE_MODEL = "fake"
ORCHESTRATOR = "orchestrator"


def _kst_midnight_utc(now: datetime) -> str:
    start = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC).isoformat()


def sessions_today(conn: sqlite3.Connection, now: datetime, *, kind: str | None = None) -> int:
    """오늘(KST) 시작한 세션 수(fake 제외, 실패 포함). kind 를 주면 그 `runs.kind` 만 센다."""
    start = _kst_midnight_utc(now)
    if kind is None:
        row = conn.execute(
            "SELECT COUNT(*) FROM agent_calls WHERE agent = ? "
            "AND COALESCE(model, '') != ? AND created_at >= ?",
            (ORCHESTRATOR, FAKE_MODEL, start),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM agent_calls a JOIN runs r ON r.id = a.run_id AND r.kind = ? "
            "WHERE a.agent = ? AND COALESCE(a.model, '') != ? AND a.created_at >= ?",
            (kind, ORCHESTRATOR, FAKE_MODEL, start),
        ).fetchone()
    return int(row[0])


def calls_today(conn: sqlite3.Connection, agent: str, now: datetime) -> int:
    """오늘(KST) 해당 agent 의 호출 수(fake 제외)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM agent_calls WHERE agent = ? AND COALESCE(model, '') != ? "
        "AND created_at >= ?",
        (agent, FAKE_MODEL, _kst_midnight_utc(now)),
    ).fetchone()
    return int(row[0])


def start_call(
    conn: sqlite3.Connection,
    run_id: int | None,
    *,
    agent: str,
    model: str,
    ticker: str | None,
    market: str | None,
    session_id: str,
    now: datetime | None = None,
    cap: int | None = None,
    extra_cap: tuple[str, int] | None = None,
) -> int | None:
    """running 행을 먼저 쓴다(크래시·동시 실행 때도 상한 집계에 잡히도록).

    cap·extra_cap 검사는 같은 쓰기 잠금(`BEGIN IMMEDIATE`) 안에서 해 두 프로세스가 마지막
    자리를 동시에 잡는 경합을 막는다. extra_cap=(runs.kind, 상한) 은 그 종류만의 상한.
    fake 모델은 상한을 보지 않는다. 상한에 닿으면 행 없이 롤백하고 None."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if model != FAKE_MODEL:
            at = now or datetime.now(UTC)
            if cap is not None and sessions_today(conn, at) >= cap:
                conn.rollback()
                log.warning("오늘 세션 상한(%d)에 도달해 세션을 띄우지 않습니다", cap)
                return None
            if extra_cap is not None:
                kind, kcap = extra_cap
                if sessions_today(conn, at, kind=kind) >= kcap:
                    conn.rollback()
                    log.warning("오늘 %s 세션 상한(%d)에 도달해 세션을 띄우지 않습니다", kind, kcap)
                    return None
        created = now.astimezone(UTC).isoformat() if now is not None else db.now_iso()
        cur = conn.execute(
            "INSERT INTO agent_calls(run_id, ticker, market, agent, model, session_id, status, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
            (run_id, ticker, market, agent, model, session_id, created),
        )
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return int(cur.lastrowid)


def finish_call(
    conn: sqlite3.Connection,
    call_id: int,
    *,
    status: str,
    session_id: str | None = None,
    cost: float | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    num_turns: int | None = None,
) -> None:
    """세션 종료 결과를 기록한다(session_id 는 주어질 때만 덮어쓴다)."""
    conn.execute(
        "UPDATE agent_calls SET status=?, session_id=COALESCE(?, session_id), cost_usd=?, "
        "duration_ms=?, error=?, num_turns=? WHERE id=?",
        (status, session_id, cost, duration_ms, error, num_turns, call_id),
    )
    conn.commit()


def close_stale_running(conn: sqlite3.Connection, *, older_than: str) -> int:
    """`older_than` 이전에 시작해 아직 running 인 세션 행을 error 로 닫는다(데몬 재기동 정리)."""
    cur = conn.execute(
        "UPDATE agent_calls SET status='error', error='데몬 재기동으로 종료' "
        "WHERE status='running' AND created_at < ?",
        (older_than,),
    )
    conn.commit()
    return cur.rowcount
