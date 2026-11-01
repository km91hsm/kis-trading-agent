"""런타임 제어 플래그(control 테이블, JSON 값)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from trader.kernel.ledger.db import now_iso


def get_flag(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM control WHERE key = ?", (key,)).fetchone()
    return default if row is None else json.loads(row["value"])


def set_flag(conn: sqlite3.Connection, key: str, value: Any) -> None:
    """upsert. 저장된 값과 같으면(JSON 문자열 비교) 쓰지 않아 updated_at 이 바뀌지 않는다."""
    conn.execute(
        "INSERT INTO control(key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at "
        "WHERE control.value IS NOT excluded.value",
        (key, json.dumps(value), now_iso()),
    )
    conn.commit()


def delete_flag(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM control WHERE key = ?", (key,))
    conn.commit()
