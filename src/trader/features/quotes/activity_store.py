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
    conn.execute(
        "INSERT INTO market_activity_batches(market,received_at,payload_json) VALUES (?,?,?)",
        (
            market,
            utc(received_at).isoformat(),
            json.dumps(partitions, ensure_ascii=False, allow_nan=False),
        ),
    )
    conn.commit()


def latest(conn: sqlite3.Connection, market: str, label: str, *, asof: datetime, success=False):
    jpath = '$."' + label + '"'
    clause = " AND json_extract(payload_json, ? || '.ok') = 1" if success else ""
    params = [market, utc(asof).isoformat(), jpath]
    if success:
        params.append(jpath)
    row = conn.execute(
        "SELECT received_at,json_extract(payload_json, ?) FROM market_activity_batches "
        "WHERE market=? AND received_at<=? AND json_type(payload_json, ?) IS NOT NULL"
        + clause
        + " ORDER BY received_at DESC,id DESC LIMIT 1",
        (jpath, *params),
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
