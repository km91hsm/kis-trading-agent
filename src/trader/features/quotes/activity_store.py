"""Append-only KIS 관측 저장과 과거 시점 조회. 모든 시간은 UTC로 정규화한다."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta


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
    row = conn.execute(
        "SELECT p.received_at,json_extract(b.payload_json, ?) "
        "FROM market_activity_partitions p "
        "JOIN market_activity_batches b ON b.id=p.batch_id "
        "WHERE p.market=? AND p.label=? AND p.received_at<=?"
        + clause
        + " ORDER BY p.received_at DESC,p.batch_id DESC LIMIT 1",
        ("$." + json.dumps(label), market, label, utc(asof).isoformat()),
    ).fetchone()
    return (row[0], json.loads(row[1])) if row else None


def history(conn: sqlite3.Connection, market: str, *, asof: datetime) -> list:
    at = utc(asof)
    rows = conn.execute(
        "SELECT received_at,payload_json FROM market_activity_batches "
        "WHERE market=? AND received_at>=? AND received_at<=? ORDER BY received_at,id",
        (market, (at - timedelta(minutes=14)).isoformat(), at.isoformat()),
    )
    return [(datetime.fromisoformat(row[0]), json.loads(row[1])) for row in rows]
