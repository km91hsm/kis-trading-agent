"""원장 DB 연결과 마이그레이션 적용. 저장 시각은 항상 UTC(KST 는 표시 전용)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from trader.kernel.platform import paths

if TYPE_CHECKING:
    from trader.kernel.ledger.migrations import Migration


class LedgerError(Exception):
    """원장·마이그레이션 계약 위반."""


def now_iso() -> str:
    """UTC ISO8601(초 정밀도, `+00:00` 표기)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    """원장 DB 연결을 연다. 기본 경로는 state_dir()/ledger.db.

    check_same_thread=False 는 웹 서버(스레드 풀)가 같은 연결 팩토리를 쓰기 위한 것이다.
    """
    db_path = path if path is not None else paths.state_dir() / "ledger.db"
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _has_version_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    return row is not None


def current_version(conn: sqlite3.Connection) -> int:
    """적용된 최신 스키마 버전. schema_version 테이블이 없으면 0(쓰지 않는다)."""
    if not _has_version_table(conn):
        return 0
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"] or 0)


def init_schema(conn: sqlite3.Connection, migs: Sequence[Migration]) -> None:
    """현재 버전보다 큰 마이그레이션만 오름차순으로 적용한다. 멱등이며 동시 실행에 안전하다.

    각 버전은 BEGIN IMMEDIATE 트랜잭션 하나(실패 시 전체 롤백)다. 이미 최신이면 쓰기 잠금 없이
    돌아온다 — 짧게 사는 tool 프로세스가 데몬의 긴 쓰기 트랜잭션에 막히지 않게 하기 위함이다.
    """
    from trader.kernel.ledger.migrations import check

    check(migs)
    conn.commit()
    if current_version(conn) >= len(migs):
        return
    for mig in migs:
        conn.execute("BEGIN IMMEDIATE")  # 쓰기 잠금 안에서 버전을 다시 읽는다
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            if mig.version <= current_version(conn):
                conn.execute("COMMIT")
                continue
            for sql in mig.statements:
                conn.execute(sql)
            if mig.fn is not None:
                mig.fn(conn)
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version(version) VALUES (?)", (mig.version,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
