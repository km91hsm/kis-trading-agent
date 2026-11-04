"""알림 기록기: 보낸 알림을 원장 notices 표에 남긴다. 실패해도 발신을 깨지 않는다."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

logger = logging.getLogger("trader.notify")


def ledger_recorder(conn_factory: Callable[[], sqlite3.Connection]) -> Callable[[dict], None]:
    def record(row: dict) -> None:
        try:
            conn = conn_factory()
            try:
                conn.execute(
                    "INSERT INTO notices(at, kind, account_id, text, channel, ok) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row["at"],
                        row["kind"],
                        row.get("account_id"),
                        row["text"],
                        row["channel"],
                        1 if row["ok"] else 0,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — 기록 실패가 알림 발신을 막으면 안 된다
            logger.warning("알림 기록 실패: %s", type(e).__name__)

    return record
