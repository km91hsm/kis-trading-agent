"""잡 큐 저장소. 웹·CLI·데몬이 요청을 원장 `job_requests` 에 넣고 데몬이 한 건씩 꺼내 실행한다.

kind 표는 여기에 없다 — 기능이 선언한 `QueueKind` 를 `kinds_of` 로 모아 `kinds` 인자로 받는다.
claim 은 조건부 UPDATE(`status='queued'`)라 enqueue/cancel 과 경합해도 한 번만 실행된다.
꺼내는 순서는 `priority DESC, id ASC`. 레인·힙 락·tick 은 이 모듈의 일이 아니다.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from trader.kernel.ledger.control import log_change
from trader.kernel.registry.types import QueueKind

Kinds = dict[str, QueueKind]


class DuplicateRequest(ValueError):
    """같은 kind+params 요청이 이미 대기·실행 중."""


class JobCancelled(Exception):
    """실행 중 종료 신호로 중단됐다 (요청은 skipped)."""


def kinds_of(features: Iterable[Any]) -> Kinds:
    """기능들이 선언한 QueueKind 를 kind 이름으로 모은다. 이름이 겹치면 ValueError."""
    out: Kinds = {}
    for f in features:
        for q in f.queue_kinds:
            if q.kind in out:
                raise ValueError(f"큐 kind 중복: {q.kind}")
            out[q.kind] = q
    return out


def validate(kinds: Kinds, kind: str, params: dict | None) -> dict:
    """kind·params 를 검증해 정규화된 dict 로 돌려준다. 잘못되면 한국어 ValueError."""
    spec = kinds.get(kind)
    if spec is None:
        raise ValueError(f"알 수 없는 작업: {kind}")
    try:
        return spec.params_model(**(params or {})).model_dump()
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", ())) or "params"
        raise ValueError(f"{kind} 파라미터 오류 ({loc}): {first.get('msg')}") from e


def canonical(params: dict) -> str:
    return json.dumps(params, sort_keys=True, ensure_ascii=False)


def job_id_for(kinds: Kinds, kind: str, params: dict) -> str:
    """하트비트·runs 에 쓰는 이름. 시장이 있으면 `{market}_{kind}`, 없으면 kind."""
    if kind not in kinds:
        raise ValueError(f"알 수 없는 작업: {kind}")
    market = params.get("market")
    return f"{str(market).lower()}_{kind}" if market else kind


def label(kinds: Kinds, kind: str) -> str:
    """화면용 한국어 이름. 선언되지 않은 kind 는 이름 그대로."""
    spec = kinds.get(kind)
    return spec.label if spec is not None else kind


def _iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat()


def enqueue(
    conn: sqlite3.Connection,
    kinds: Kinds,
    kind: str,
    params: dict | None,
    *,
    source: str,
    now: datetime,
    priority: int | None = None,
) -> int:
    """검증 → 중복(queued|running) 검사 → 삽입. 반환은 요청 id. priority 가 None 이면
    `kinds[kind].priority`."""
    p = validate(kinds, kind, params)
    prio = kinds[kind].priority if priority is None else int(priority)
    key = canonical(p)
    if conn.in_transaction:
        conn.commit()
    # 중복 검사와 삽입을 한 쓰기 잠금 안에서 — 동시에 같은 요청을 넣어도 하나만 들어간다
    conn.execute("BEGIN IMMEDIATE")
    try:
        dup = conn.execute(
            "SELECT id FROM job_requests WHERE kind=? AND params=? "
            "AND status IN ('queued','running') LIMIT 1",
            (kind, key),
        ).fetchone()
        if dup is not None:
            raise DuplicateRequest(f"같은 작업이 이미 대기·실행 중이에요 (#{dup['id']})")
        cur = conn.execute(
            "INSERT INTO job_requests(kind, params, status, source, requested_at, priority) "
            "VALUES (?, ?, 'queued', ?, ?, ?)",
            (kind, key, source, _iso(now), prio),
        )
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return int(cur.lastrowid)


def claim_next(conn: sqlite3.Connection, *, now: datetime, kinds: Iterable[str]) -> dict | None:
    """주어진 kind 중 우선순위가 높은 것, 같으면 가장 오래된 queued 를 running 으로 바꿔 돌려준다.
    다른 연결이 먼저 가져갔으면 다음 건. kind 가 비어 있으면 None."""
    names = sorted(set(kinds))
    if not names:
        return None
    where = f"status='queued' AND kind IN ({','.join('?' * len(names))})"
    while True:
        row = conn.execute(
            f"SELECT id FROM job_requests WHERE {where} ORDER BY priority DESC, id LIMIT 1",
            names,
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE job_requests SET status='running', started_at=? WHERE id=? AND status='queued'",
            (_iso(now), row["id"]),
        )
        conn.commit()
        if cur.rowcount == 1:
            got = conn.execute("SELECT * FROM job_requests WHERE id=?", (row["id"],)).fetchone()
            return _hydrate(got)


def set_progress(conn: sqlite3.Connection, req_id: int, text: str) -> None:
    conn.execute("UPDATE job_requests SET progress=? WHERE id=?", (text[:500], req_id))
    conn.commit()


def finish(
    conn: sqlite3.Connection,
    req_id: int,
    status: Literal["ok", "error", "skipped"],
    *,
    result: dict | None = None,
    error: str | None = None,
    now: datetime,
) -> None:
    conn.execute(
        "UPDATE job_requests SET status=?, finished_at=?, error=?, result=? WHERE id=?",
        (
            status,
            _iso(now),
            error,
            json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
            req_id,
        ),
    )
    conn.commit()


def cancel(conn: sqlite3.Connection, req_id: int, *, now: datetime | None = None) -> bool:
    """대기 중(queued)만 취소 → skipped('취소'). 실행 중이면 False."""
    when = _iso(now if now is not None else datetime.now(UTC))
    cur = conn.execute(
        "UPDATE job_requests SET status='skipped', finished_at=?, error='취소' "
        "WHERE id=? AND status='queued'",
        (when, req_id),
    )
    conn.commit()
    return cur.rowcount == 1


# 조작 이력에 남기지 않는 파라미터 이름 조각(소문자 비교). 큐 kind 파라미터는 시장·종목·계좌 id 라
# 지금은 걸리는 것이 없지만, 비밀이 섞인 kind 가 생겨도 이력에 값이 남지 않게 한다.
SENSITIVE_PARAM_MARKS = ("secret", "token", "password", "passwd", "app_key", "appkey")


def public_params(params: dict | None) -> dict:
    """조작 이력용 파라미터 사본: 이름에 `SENSITIVE_PARAM_MARKS` 가 들어간 필드는 뺀다."""
    return {
        k: v
        for k, v in (params or {}).items()
        if not any(m in str(k).lower() for m in SENSITIVE_PARAM_MARKS)
    }


def log_request(
    conn: sqlite3.Connection,
    *,
    kind: str,
    req_id: int,
    params: dict | None,
    channel: str,
    actor: str | None,
    now: datetime,
) -> int:
    """수동 작업 요청의 조작 이력(control_log `job:<kind>`, new = {request_id, 민감 아닌
    params})."""
    new = {"request_id": req_id, "params": public_params(params)}
    return log_change(
        conn, channel=channel, actor=actor, key=f"job:{kind}", old=None, new=new, at=_iso(now)
    )


def log_cancel(
    conn: sqlite3.Connection, req_id: int, *, channel: str, actor: str | None, now: datetime
) -> int | None:
    """취소한 요청의 조작 이력(`job:<kind>` queued → cancelled). 요청이 없으면 None."""
    row = conn.execute("SELECT kind FROM job_requests WHERE id=?", (req_id,)).fetchone()
    if row is None:
        return None
    return log_change(
        conn, channel=channel, actor=actor, key=f"job:{row[0]}", old="queued", new="cancelled",
        at=_iso(now),
    )  # fmt: skip


def mark_stale_running(conn: sqlite3.Connection, *, now: datetime, reason: str) -> int:
    """데몬 기동 때: 이전 프로세스가 남긴 running 을 error 로 닫는다(여기서 재실행하지 않는다)."""
    cur = conn.execute(
        "UPDATE job_requests SET status='error', finished_at=?, error=? WHERE status='running'",
        (_iso(now), reason),
    )
    conn.commit()
    return cur.rowcount


def expire_queued(conn: sqlite3.Connection, *, older_than: datetime) -> int:
    """오래 대기한 요청은 skipped('만료'). 데몬이 꺼진 동안 쌓인 요청이 한꺼번에 돌지 않도록."""
    cur = conn.execute(
        "UPDATE job_requests SET status='skipped', finished_at=?, error='만료' "
        "WHERE status='queued' AND requested_at < ?",
        (datetime.now(UTC).isoformat(), _iso(older_than)),
    )
    conn.commit()
    return cur.rowcount


def _hydrate(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["params"] = json.loads(d["params"]) if d.get("params") else {}
    d["result"] = json.loads(d["result"]) if d.get("result") else None
    return d


def list_recent(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """running, 그 다음 queued(실행될 순서 = priority DESC, id), 그 뒤 최근 종료분(최신 순)."""
    active = conn.execute(
        "SELECT * FROM job_requests WHERE status IN ('queued','running') "
        "ORDER BY status='running' DESC, priority DESC, id"
    ).fetchall()
    done = conn.execute(
        "SELECT * FROM job_requests WHERE status NOT IN ('queued','running') "
        "ORDER BY id DESC LIMIT ?",
        (max(0, limit - len(active)),),
    ).fetchall()
    return [_hydrate(r) for r in [*active, *done]]


def active_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM job_requests WHERE status IN ('queued','running')"
    ).fetchone()
    return int(row["n"])


def latest_by(conn: sqlite3.Connection, kind: str, source: str | None = None) -> dict | None:
    """kind(·source) 의 가장 최근 요청 (부트스트랩 재시도 제한용)."""
    sql = "SELECT * FROM job_requests WHERE kind=?"
    args: list = [kind]
    if source:
        sql += " AND source=?"
        args.append(source)
    row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
    return _hydrate(row) if row else None
