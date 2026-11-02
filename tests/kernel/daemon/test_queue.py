"""잡 큐 저장소(queue.py): kind 검증·중복 거부·우선순위 claim·진행/종료·stale 정리."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.daemon import queue
from trader.kernel.ledger import db
from trader.kernel.registry.contracts import all_migrations
from trader.kernel.registry.discover import discover
from trader.kernel.registry.types import Feature, QueueKind

NOW = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)


class ReevalParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: str
    ticker: str
    trigger_id: int = Field(0, ge=0)


class PlainParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: str | None = None


def _body(ctx, req):
    return {}


KINDS = {
    "reeval": QueueKind("reeval", ReevalParams, "재평가", "reeval", 10, True, False, _body),
    "plain": QueueKind("plain", PlainParams, "일반 작업", "slow", 0, False, False, _body),
}
ALL = list(KINDS)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_schema(c, all_migrations(discover()))
    yield c
    c.close()


def enq(conn, kind, params=None, **kw):
    kw.setdefault("source", "web")
    kw.setdefault("now", NOW)
    return queue.enqueue(conn, KINDS, kind, params or {}, **kw)


def test_validate_rejects_unknown_kind_and_extra_params():
    with pytest.raises(ValueError, match="알 수 없는 작업"):
        queue.validate(KINDS, "nope", {})
    with pytest.raises(ValueError):
        queue.validate(KINDS, "plain", {"bogus": 1})
    with pytest.raises(ValueError):
        queue.validate(KINDS, "reeval", {})  # market·ticker 필수
    with pytest.raises(ValueError):
        queue.validate(KINDS, "reeval", {"market": "KR", "ticker": "x", "trigger_id": -1})
    assert queue.validate(KINDS, "plain", {}) == {"market": None}
    assert queue.validate(KINDS, "reeval", {"market": "US", "ticker": "AAPL"}) == {
        "market": "US",
        "ticker": "AAPL",
        "trigger_id": 0,
    }


def test_kinds_of_collects_and_rejects_duplicates():
    f1 = Feature(id="a", tier="lane", queue_kinds=(KINDS["reeval"],))
    f2 = Feature(id="b", tier="lane", queue_kinds=(KINDS["plain"],))
    assert queue.kinds_of([f1, f2]) == KINDS
    with pytest.raises(ValueError):
        queue.kinds_of([f1, f1])


def test_enqueue_dedupes_queued_and_running(conn):
    a = enq(conn, "plain", {"market": "KR"})
    with pytest.raises(queue.DuplicateRequest):
        enq(conn, "plain", {"market": "KR"})
    enq(conn, "plain", {"market": "US"})  # 다른 파라미터는 별개
    row = queue.claim_next(conn, now=NOW, kinds=ALL)
    assert row["id"] == a and row["status"] == "running"
    with pytest.raises(queue.DuplicateRequest):  # running 도 중복
        enq(conn, "plain", {"market": "KR"})
    queue.finish(conn, a, "ok", result={"n": 1}, now=NOW)
    assert enq(conn, "plain", {"market": "KR"}) > a


def test_claim_next_is_fifo_and_conditional(conn):
    first = enq(conn, "plain", {"market": "KR"})
    second = enq(conn, "plain", {"market": "US"})
    # 다른 프로세스가 first 를 먼저 가져갔다
    conn.execute("UPDATE job_requests SET status='running' WHERE id=?", (first,))
    conn.commit()
    row = queue.claim_next(conn, now=NOW, kinds=ALL)
    assert row["id"] == second
    assert queue.claim_next(conn, now=NOW, kinds=ALL) is None


def test_claim_next_filters_by_kinds(conn):
    enq(conn, "plain")
    assert queue.claim_next(conn, now=NOW, kinds=[]) is None
    assert queue.claim_next(conn, now=NOW, kinds=["reeval"]) is None
    assert queue.claim_next(conn, now=NOW, kinds=["plain"])["kind"] == "plain"


def test_finish_and_progress_roundtrip(conn):
    rid = enq(conn, "plain", {"market": "KR"}, source="daemon")
    queue.claim_next(conn, now=NOW, kinds=ALL)
    queue.set_progress(conn, rid, "KR 005930: 10행")
    rows = queue.list_recent(conn)
    assert rows[0]["progress"] == "KR 005930: 10행"
    assert rows[0]["params"] == {"market": "KR"}
    queue.finish(conn, rid, "error", error="boom", now=NOW)
    r = queue.list_recent(conn)[0]
    assert r["status"] == "error" and r["error"] == "boom" and r["finished_at"]
    assert queue.active_count(conn) == 0


def test_cancel_only_queued(conn):
    rid = enq(conn, "plain", {"market": "KR"})
    assert queue.cancel(conn, rid) is True
    assert queue.list_recent(conn)[0]["status"] == "skipped"
    rid2 = enq(conn, "plain", {"market": "KR"})
    queue.claim_next(conn, now=NOW, kinds=ALL)
    assert queue.cancel(conn, rid2) is False


def test_mark_stale_running_and_expire_queued(conn):
    old = NOW - timedelta(hours=7)
    a = enq(conn, "plain", {"market": "KR"}, now=old)
    queue.claim_next(conn, now=old, kinds=ALL)
    b = enq(conn, "plain", {"market": "US"}, now=old)
    c = enq(conn, "plain", {}, now=NOW - timedelta(hours=1))
    assert queue.mark_stale_running(conn, now=NOW, reason="데몬 재시작으로 중단") == 1
    assert queue.expire_queued(conn, older_than=NOW - timedelta(hours=6)) == 1
    by = {r["id"]: r for r in queue.list_recent(conn)}
    assert by[a]["status"] == "error" and "재시작" in by[a]["error"]
    assert by[b]["status"] == "skipped" and by[c]["status"] == "queued"


def test_list_recent_orders_active_first(conn):
    done = enq(conn, "plain", {"market": "KR"})
    queue.claim_next(conn, now=NOW, kinds=ALL)
    queue.finish(conn, done, "ok", now=NOW)
    q = enq(conn, "plain", {"market": "US"})
    ids = [r["id"] for r in queue.list_recent(conn)]
    assert ids == [q, done]


def test_latest_by(conn):
    assert queue.latest_by(conn, "plain") is None
    enq(conn, "plain", {"market": "KR"}, source="web")
    b = enq(conn, "plain", {"market": "US"}, source="daemon")
    assert queue.latest_by(conn, "plain")["id"] == b
    assert queue.latest_by(conn, "plain", "web")["params"] == {"market": "KR"}


def test_job_id_for_maps_scheduled_kinds():
    assert queue.job_id_for(KINDS, "plain", {"market": "KR"}) == "kr_plain"
    assert queue.job_id_for(KINDS, "plain", {}) == "plain"
    assert queue.job_id_for(KINDS, "reeval", {"market": "US", "ticker": "A"}) == "us_reeval"


def test_label_is_korean():
    assert queue.label(KINDS, "reeval") == "재평가"
    assert queue.label(KINDS, "plain") == "일반 작업"
    assert queue.label(KINDS, "unknown") == "unknown"


def test_schema_has_table(conn):
    assert db.current_version(conn) >= 8
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_requests'"
    ).fetchone()
    assert row is not None


def test_claim_next_prefers_priority_then_fifo(conn):
    """재평가(priority 10)는 먼저 넣은 일반 작업보다 먼저 꺼내진다. 같은 우선순위는 FIFO."""
    bf = enq(conn, "plain", {"market": "KR"})
    tr = enq(conn, "plain", {"market": "US"})
    re = enq(
        conn, "reeval", {"market": "KR", "ticker": "005930", "trigger_id": 1}, source="daemon"
    )
    ids = [r["id"] for r in queue.list_recent(conn)]
    assert ids == [re, bf, tr]  # 실행될 순서
    assert queue.claim_next(conn, now=NOW, kinds=ALL)["id"] == re
    assert queue.claim_next(conn, now=NOW, kinds=ALL)["id"] == bf
    # 명시 우선순위
    hi = enq(conn, "plain", {}, source="cli", priority=5)
    assert queue.claim_next(conn, now=NOW, kinds=ALL)["id"] == hi
    assert queue.claim_next(conn, now=NOW, kinds=ALL)["id"] == tr


def test_enqueue_concurrent_same_request_inserts_once(tmp_path):
    """두 연결 이상이 동시에 같은 요청을 넣어도 한 건만 들어간다."""
    path = tmp_path / "c.db"
    c0 = db.connect(path)
    db.init_schema(c0, all_migrations(discover()))
    c0.close()
    results, barrier = [], threading.Barrier(4)

    def worker():
        c = db.connect(path)
        try:
            barrier.wait()
            results.append(queue.enqueue(c, KINDS, "plain", {}, source="web", now=NOW))
        except queue.DuplicateRequest:
            results.append("dup")
        finally:
            c.close()

    ths = [threading.Thread(target=worker) for _ in range(4)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert sum(1 for r in results if r != "dup") == 1 and results.count("dup") == 3
