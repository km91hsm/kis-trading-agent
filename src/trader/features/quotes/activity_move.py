"""원장에 남은 시장 순위 관측을 보관소(`market_activity.db`)로 옮긴다 — 데몬 기동 훅 전용.

마이그레이션으로 하지 않는 이유: 마이그레이션은 원장을 여는 **어떤 프로세스든**(watchdog·세션
도구·CLI) 먼저 뜨는 쪽이 돌린다. 약 1GB 를 복사하는 동안 원장 쓰기 잠금을 쥐면 살아 있는 데몬의
주문 기록이 막히고, 옛 버전 데몬이 도는 채로 테이블이 사라질 수 있다. 여기서는 데몬 잠금을 쥔
프로세스만, 스케줄러가 돌기 전에 옮긴다:

1. 원장을 읽기만 하며 보관소에 복사한다(같은 id, 재실행해도 중복 없음 — 쓰기 잠금 없음).
2. 보관소 건수를 확인한다. 같은 id 인데 내용이 다른 행이 있으면(되돌렸다 다시 올린 경우) 멈춘다.
3. 짧은 트랜잭션 하나로 원장의 두 테이블을 지운다.
4. 옮긴 행이 있었으면 VACUUM 으로 원장 파일을 줄인다.

실패는 기동을 막지 않는다(원장은 그대로, 다음 기동에 다시). 관측은 한 행도 버리지 않는다.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from typing import Any

from trader.features.quotes import activity_store
from trader.kernel.ledger import db

logger = logging.getLogger("trader.quotes")

_COPY_CHUNK = 200
_TRIGGERS = (
    "activity_partition_no_update",
    "activity_partition_no_delete",
    "activity_no_update",
    "activity_no_delete",
)


class MoveConflict(Exception):
    """보관소에 같은 id 로 다른 관측이 이미 있다 — 자동으로 합치지 않는다."""


def _has_tables(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_activity_batches'"
        ).fetchone()
        is not None
    )


def _copy_batches(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
    cur = src.execute(
        "SELECT id,market,received_at,payload_json FROM market_activity_batches ORDER BY id"
    )
    while rows := cur.fetchmany(_COPY_CHUNK):
        ids = [r[0] for r in rows]
        marks = ",".join("?" * len(ids))
        have = {
            r[0]: (r[1], r[2])
            for r in dst.execute(
                "SELECT id,market,received_at FROM market_activity_batches "  # noqa: S608
                f"WHERE id IN ({marks})",
                ids,
            )
        }
        for r in rows:
            if r[0] in have and have[r[0]] != (r[1], r[2]):
                raise MoveConflict(
                    f"배치 id {r[0]}: 원장 {r[1]} {r[2]} ≠ 보관소 {have[r[0]][0]} {have[r[0]][1]}"
                )
        dst.executemany(
            "INSERT OR IGNORE INTO market_activity_batches(id,market,received_at,payload_json) "
            "VALUES (?,?,?,?)",
            [tuple(r) for r in rows if r[0] not in have],
        )
        dst.commit()


def _copy_partitions(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
    cur = src.execute(
        "SELECT batch_id,market,label,ok,received_at FROM market_activity_partitions "
        "ORDER BY batch_id,label"
    )
    while rows := cur.fetchmany(_COPY_CHUNK * 10):
        dst.executemany(
            "INSERT OR IGNORE INTO market_activity_partitions"
            "(batch_id,market,label,ok,received_at) VALUES (?,?,?,?,?)",
            [tuple(r) for r in rows],
        )
        dst.commit()


def move_out(conn: sqlite3.Connection) -> dict[str, Any]:
    """원장 연결의 순위 관측을 보관소로 옮기고 원장 테이블을 지운다. 반환
    `{"moved": 옮긴 배치 수, "dropped": 테이블을 지웠나}`. 옮길 것이 없으면 둘 다 거짓/0.
    확인이 안 되면 예외(원장은 그대로)."""
    path = activity_store.file_of(conn)
    if path is None or path.name == activity_store.ARCHIVE_NAME or not _has_tables(conn):
        return {"moved": 0, "dropped": False}
    conn.commit()
    n_batches, max_id = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM market_activity_batches"
    ).fetchone()
    if n_batches:
        logger.info("순위 관측 %d건을 보관소로 옮깁니다(원장에서 분리)", n_batches)
        with closing(db.connect(activity_store.archive_path(path))) as dst:
            activity_store.ensure_schema(dst)
            _copy_batches(conn, dst)
            _copy_partitions(conn, dst)
    # 확인과 삭제는 한 쓰기 트랜잭션: 그 사이 원장에 새 행이 끼어들 수 없다
    conn.execute("BEGIN IMMEDIATE")
    try:
        now_b = conn.execute("SELECT COUNT(*) FROM market_activity_batches").fetchone()[0]
        now_p = conn.execute("SELECT COUNT(*) FROM market_activity_partitions").fetchone()[0]
        if now_b:
            with closing(db.connect(activity_store.archive_path(path))) as dst:
                got_b = dst.execute(
                    "SELECT COUNT(*) FROM market_activity_batches WHERE id <= ?", (max_id,)
                ).fetchone()[0]
                got_p = dst.execute(
                    "SELECT COUNT(*) FROM market_activity_partitions WHERE batch_id <= ?",
                    (max_id,),
                ).fetchone()[0]
            if now_b != n_batches or got_b < now_b or got_p < now_p:
                raise RuntimeError(
                    f"순위 관측 이전 확인 실패: 배치 {got_b}/{now_b}, 조각 {got_p}/{now_p}"
                )
        for trigger in _TRIGGERS:
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        conn.execute("DROP TABLE market_activity_partitions")
        conn.execute("DROP TABLE market_activity_batches")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"moved": int(n_batches), "dropped": True}
