"""KIS 시장 활동의 수신 이력. 기존 관측은 수정·삭제하지 않는다.

버전 16 이 원장 안에 만든 두 테이블은 버전 21 이 보관소 파일(`market_activity.db`,
`activity_store`)로 옮기고 원장에서 지운다 — 관측 자체는 한 행도 버리지 않는다."""

import logging
import sqlite3
from contextlib import closing

from trader.features.quotes import activity_store
from trader.kernel.ledger import db
from trader.kernel.ledger.db import LedgerError
from trader.kernel.ledger.migrations import Migration

logger = logging.getLogger("trader.quotes")

VACUUM_FLAG = "ledger_vacuum_pending"  # 원장 빈 공간 회수 예약(데몬 기동 훅이 처리)
_COPY_CHUNK = 200


def _copy(src: sqlite3.Connection, dst: sqlite3.Connection, table: str, cols: str) -> None:
    marks = ",".join("?" * len(cols.split(",")))
    cur = src.execute(f"SELECT {cols} FROM {table} ORDER BY 1")  # noqa: S608 - 고정 이름
    while rows := cur.fetchmany(_COPY_CHUNK):
        dst.executemany(
            f"INSERT OR IGNORE INTO {table}({cols}) VALUES ({marks})",  # noqa: S608
            [tuple(r) for r in rows],
        )
        dst.commit()


def move_activity_out(conn: sqlite3.Connection) -> None:
    """원장의 순위 관측을 보관소 파일로 옮기고(같은 id, 재실행해도 중복 없음) 건수를 확인한 뒤에만
    원장의 두 테이블을 지운다. 확인이 안 되면 LedgerError 로 버전 전체를 되돌린다(원장 그대로).
    파일이 없는 원장(메모리 DB)은 그 연결이 곧 보관소라 건드리지 않는다."""
    path = activity_store.file_of(conn)
    if path is None:
        return
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_activity_batches'"
    ).fetchone()
    if exists is None:
        return
    n_batches, max_id = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM market_activity_batches"
    ).fetchone()
    n_parts = conn.execute("SELECT COUNT(*) FROM market_activity_partitions").fetchone()[0]
    if n_batches:
        logger.info("순위 관측 %d건을 보관소로 옮깁니다(원장에서 분리)", n_batches)
        with closing(db.connect(activity_store.archive_path(path))) as dst:
            activity_store.ensure_schema(dst)
            _copy(conn, dst, "market_activity_batches", "id,market,received_at,payload_json")
            _copy(
                conn, dst, "market_activity_partitions", "batch_id,market,label,ok,received_at"
            )
            got_b = dst.execute(
                "SELECT COUNT(*) FROM market_activity_batches WHERE id <= ?", (max_id,)
            ).fetchone()[0]
            got_p = dst.execute(
                "SELECT COUNT(*) FROM market_activity_partitions WHERE batch_id <= ?", (max_id,)
            ).fetchone()[0]
        if got_b < n_batches or got_p < n_parts:
            raise LedgerError(
                f"순위 관측 이전 확인 실패: 배치 {got_b}/{n_batches}, 조각 {got_p}/{n_parts} "
                "(원장은 그대로 둡니다)"
            )
    for trigger in (
        "activity_partition_no_update",
        "activity_partition_no_delete",
        "activity_no_update",
        "activity_no_delete",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    conn.execute("DROP TABLE market_activity_partitions")
    conn.execute("DROP TABLE market_activity_batches")
    if n_batches:
        conn.execute(
            "INSERT OR REPLACE INTO control(key, value, updated_at) VALUES (?, 'true', ?)",
            (VACUUM_FLAG, db.now_iso()),
        )


MIGRATIONS = (
    Migration(
        16,
        "quotes-market-activity",
        (
            """CREATE TABLE market_activity_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL CHECK(market IN ('KR','US')),
                received_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )""",
            "CREATE INDEX ix_activity_market_received "
            "ON market_activity_batches(market, received_at)",
            """CREATE TABLE market_activity_partitions (
                batch_id INTEGER NOT NULL REFERENCES market_activity_batches(id),
                market TEXT NOT NULL CHECK(market IN ('KR','US')),
                label TEXT NOT NULL,
                ok INTEGER NOT NULL CHECK(ok IN (0,1)),
                received_at TEXT NOT NULL,
                PRIMARY KEY (batch_id, label)
            )""",
            "CREATE INDEX ix_activity_partition_latest "
            "ON market_activity_partitions(market,label,received_at DESC,batch_id DESC)",
            "CREATE INDEX ix_activity_partition_success "
            "ON market_activity_partitions(market,label,ok,received_at DESC,batch_id DESC)",
            """CREATE TRIGGER activity_partition_no_update
                BEFORE UPDATE ON market_activity_partitions
                BEGIN SELECT RAISE(ABORT, 'market activity partitions are append-only'); END""",
            """CREATE TRIGGER activity_partition_no_delete
                BEFORE DELETE ON market_activity_partitions
                BEGIN SELECT RAISE(ABORT, 'market activity partitions are append-only'); END""",
            """CREATE TRIGGER activity_no_update BEFORE UPDATE ON market_activity_batches
                BEGIN SELECT RAISE(ABORT, 'market activity is append-only'); END""",
            """CREATE TRIGGER activity_no_delete BEFORE DELETE ON market_activity_batches
                BEGIN SELECT RAISE(ABORT, 'market activity is append-only'); END""",
        ),
        owner="quotes",
    ),
    Migration(21, "quotes-activity-archive-split", fn=move_activity_out, owner="quotes"),
)
