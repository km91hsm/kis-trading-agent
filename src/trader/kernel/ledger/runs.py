"""실행 기록(runs 테이블) 시작·종료."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from trader.kernel.ledger.db import now_iso


def start(conn: sqlite3.Connection, kind: str, market: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO runs(kind, market, started_at, status) VALUES (?, ?, ?, 'running')",
        (kind, market, now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    *,
    error: str | None = None,
    result: Any = None,
) -> None:
    """status 는 'ok'|'error'|'skipped'. result 는 JSON 으로 직렬화해 result_json 에 쓴다."""
    result_json = None if result is None else json.dumps(result, ensure_ascii=False)
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, error=?, result_json=? WHERE id=?",
        (now_iso(), status, error, result_json, run_id),
    )
    conn.commit()


def close_running(conn: sqlite3.Connection, *, reason: str) -> int:
    """running 으로 남은 실행 기록을 error 로 닫고 건수를 돌려준다(데몬 기동 정리 — 잠금을 잡은
    뒤라 이전 데몬의 잡은 모두 죽었다). 아직 살아 있는 다른 프로세스의 세션 행이 섞여 있어도
    그 세션이 끝나면 `finish` 가 결과로 덮어쓴다."""
    cur = conn.execute(
        "UPDATE runs SET status='error', finished_at=?, error=? WHERE status='running'",
        (now_iso(), reason),
    )
    conn.commit()
    return cur.rowcount
