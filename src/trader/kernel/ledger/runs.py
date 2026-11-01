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
