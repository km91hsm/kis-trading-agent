"""Append-only KIS 관측 저장과 과거 시점 조회. 모든 시간은 UTC로 정규화한다.

관측 원본은 원장(`trader.db`)이 아니라 같은 폴더의 보관소 `market_activity.db` 에 쌓는다 — 하루
수백 MB 씩 늘어 원장과 일일 백업을 키우던 것을 떼어 냈다(영구 보관, 수정·삭제 금지는 그대로).
옛 원장에 남은 행은 데몬 기동 훅이 옮긴다(`activity_move`). 보관소는 일일 백업 대상이 아니다.
호출자는 지금처럼 **원장 연결**을 넘기고, 여기서 그 파일 옆의 보관소를 연다. 파일이 없는 연결
(메모리 DB)과 보관소 연결 자체는 그 연결을 그대로 쓴다."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trader.kernel.ledger import db

ARCHIVE_NAME = "market_activity.db"

# 마이그레이션 16(`quotes-market-activity`)과 같은 모양 — 보관소 파일에 직접 만든다
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS market_activity_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market TEXT NOT NULL CHECK(market IN ('KR','US')),
        received_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_activity_market_received "
    "ON market_activity_batches(market, received_at)",
    """CREATE TABLE IF NOT EXISTS market_activity_partitions (
        batch_id INTEGER NOT NULL REFERENCES market_activity_batches(id),
        market TEXT NOT NULL CHECK(market IN ('KR','US')),
        label TEXT NOT NULL,
        ok INTEGER NOT NULL CHECK(ok IN (0,1)),
        received_at TEXT NOT NULL,
        PRIMARY KEY (batch_id, label)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_activity_partition_latest "
    "ON market_activity_partitions(market,label,received_at DESC,batch_id DESC)",
    "CREATE INDEX IF NOT EXISTS ix_activity_partition_success "
    "ON market_activity_partitions(market,label,ok,received_at DESC,batch_id DESC)",
    """CREATE TRIGGER IF NOT EXISTS activity_partition_no_update
        BEFORE UPDATE ON market_activity_partitions
        BEGIN SELECT RAISE(ABORT, 'market activity partitions are append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS activity_partition_no_delete
        BEFORE DELETE ON market_activity_partitions
        BEGIN SELECT RAISE(ABORT, 'market activity partitions are append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS activity_no_update BEFORE UPDATE ON market_activity_batches
        BEGIN SELECT RAISE(ABORT, 'market activity is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS activity_no_delete BEFORE DELETE ON market_activity_batches
        BEGIN SELECT RAISE(ABORT, 'market activity is append-only'); END""",
)

_ready: set[str] = set()  # 스키마를 확인한 보관소 파일(프로세스당 1회)
_ready_lock = threading.Lock()


def ensure_schema(conn: sqlite3.Connection) -> None:
    for sql in SCHEMA:
        conn.execute(sql)
    conn.commit()


def file_of(conn: sqlite3.Connection) -> Path | None:
    """연결의 main DB 파일. 메모리 DB 면 None."""
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            return Path(row[2]) if row[2] else None
    return None


def archive_path(ledger_file: Path) -> Path:
    return ledger_file.with_name(ARCHIVE_NAME)


@contextmanager
def archive(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """원장 연결 → 보관소 연결(쓰고 닫는다). 메모리 DB·보관소 연결이면 그 연결 그대로."""
    path = file_of(conn)
    if path is None or path.name == ARCHIVE_NAME:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='market_activity_batches'"
        ).fetchone() is None:
            ensure_schema(conn)
        yield conn
        return
    target = archive_path(path)
    key = str(target)
    # 파일 유무는 연결 **전에** 본다(연결이 빈 파일을 만든다). 실행 중에 보관소를 옮기거나 지웠으면
    # 스키마를 다시 만들고, 옛 파일의 배치 id 로 남은 이력 캐시도 버린다
    with _ready_lock:
        fresh = key not in _ready or not target.exists()
    out = db.connect(target)
    try:
        if fresh:
            ensure_schema(out)
            with _ready_lock:
                _ready.add(key)
            with _history_lock:
                for k in [k for k in _history_cache if k[0] == key]:
                    del _history_cache[k]
        yield out
    finally:
        out.close()


def utc(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("activity asof/received_at은 timezone이 필요합니다")
    return at.astimezone(UTC)


def append(
    conn: sqlite3.Connection, market: str, *, received_at: datetime, partitions: dict
) -> None:
    if market not in {"KR", "US"}:
        raise ValueError("market은 KR 또는 US여야 합니다")
    stamp = utc(received_at).isoformat()
    payload = json.dumps(partitions, ensure_ascii=False, allow_nan=False)
    with archive(conn) as store:
        _append(store, market, stamp, payload, partitions)


def _append(
    conn: sqlite3.Connection, market: str, stamp: str, payload: str, partitions: dict
) -> None:
    # Archive and its searchable metadata are one transaction: neither is useful alone.
    with conn:
        cur = conn.execute(
            "INSERT INTO market_activity_batches(market,received_at,payload_json) VALUES (?,?,?)",
            (market, stamp, payload),
        )
        conn.executemany(
            "INSERT INTO market_activity_partitions(batch_id,market,label,ok,received_at) "
            "VALUES (?,?,?,?,?)",
            [
                (cur.lastrowid, market, label, int(part.get("ok") == 1), stamp)
                for label, part in partitions.items()
            ],
        )


def latest(conn: sqlite3.Connection, market: str, label: str, *, asof: datetime, success=False):
    # Both paths search a partition/time index. Even a never-successful partition takes
    # one index seek, rather than decoding every historical archive looking for ok=true.
    clause = " AND p.ok=1" if success else ""
    with archive(conn) as store:
        row = _latest_row(store, market, label, asof, clause)
    return (row[0], json.loads(row[1])) if row else None


def _latest_row(conn: sqlite3.Connection, market: str, label: str, asof: datetime, clause: str):
    return conn.execute(
        "SELECT p.received_at,json_extract(b.payload_json, ?) "
        "FROM market_activity_partitions p "
        "JOIN market_activity_batches b ON b.id=p.batch_id "
        "WHERE p.market=? AND p.label=? AND p.received_at<=?"
        + clause
        + " ORDER BY p.received_at DESC,p.batch_id DESC LIMIT 1",
        ("$." + json.dumps(label), market, label, utc(asof).isoformat()),
    ).fetchone()


HISTORY_WINDOW = timedelta(minutes=14)
HISTORY_CACHE_MAX = 60  # 시장당 메모리에 두는 배치 수 상한(30초 주기 14분 ≈ 28개)
# (보관소 파일, 시장) → {배치 id: (수신 시각, 파싱한 payload)}. 배치는 append-only 라 한 번 파싱한
# 결과를 그대로 다시 쓸 수 있다. 창 밖 배치는 조회 때 버려 14분치만 남는다.
_history_cache: dict[tuple[str, str], dict[int, tuple[datetime, dict]]] = {}
_history_lock = threading.Lock()


def history(conn: sqlite3.Connection, market: str, *, asof: datetime) -> list:
    """asof 직전 14분의 배치 [(수신 시각, payload)] (오래된 순). 30초마다 불리므로 이미 읽은
    배치는 다시 읽거나 파싱하지 않는다(파일 보관소일 때 — 메모리 DB 는 매번 읽는다)."""
    at = utc(asof)
    lo, hi = (at - HISTORY_WINDOW).isoformat(), at.isoformat()
    with archive(conn) as store:
        path = file_of(store)
        key = (str(path), market) if path is not None else None
        with _history_lock:
            cached = dict(_history_cache.get(key, {})) if key is not None else {}
        ids = store.execute(
            "SELECT id,received_at FROM market_activity_batches "
            "WHERE market=? AND received_at>=? AND received_at<=? ORDER BY received_at,id",
            (market, lo, hi),
        ).fetchall()
        missing = [row[0] for row in ids if row[0] not in cached]
        for i in range(0, len(missing), 200):
            chunk = missing[i : i + 200]
            marks = ",".join("?" * len(chunk))
            for row in store.execute(
                "SELECT id,received_at,payload_json FROM market_activity_batches "  # noqa: S608
                f"WHERE id IN ({marks})",
                chunk,
            ):
                cached[row[0]] = (datetime.fromisoformat(row[1]), json.loads(row[2]))
    if key is not None:
        floor = (at - HISTORY_WINDOW).isoformat()
        with _history_lock:
            # 더 늦은 시점을 본 호출이 채운 항목을 지우지 않게 합친 뒤, 이번 창보다 오래된
            # 것만 버린다
            merged = {**_history_cache.get(key, {}), **cached}
            kept = {k: v for k, v in merged.items() if v[0].isoformat() >= floor}
            # asof 가 과거로 내려가는 조회(백테스트)는 위 기준으로 아무것도 버리지 않는다 —
            # 건수 상한으로 최신 것만 남긴다
            for k in sorted(kept)[:-HISTORY_CACHE_MAX]:
                del kept[k]
            _history_cache[key] = kept
    return [cached[row[0]] for row in ids]
