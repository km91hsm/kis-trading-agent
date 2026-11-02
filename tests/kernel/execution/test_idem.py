from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trader.kernel.execution.idem import ExecKeyStore

T0 = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
NOW = T0.isoformat()


def _order(conn, status="accepted", created_at=NOW):
    cur = conn.execute(
        "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, status, mode, "
        "created_at) VALUES ('a','005930','KR','BUY',1,'LIMIT',?, 'auto', ?)",
        (status, created_at),
    )
    conn.commit()
    return cur.lastrowid


def _approval(conn, status="pending", expires=None):
    cur = conn.execute(
        "INSERT INTO approvals(account_id, order_json, status, requested_at, expires_at, "
        "fingerprint) VALUES ('a','{}',?,?,?,'fp')",
        (status, NOW, expires or (T0 + timedelta(minutes=30)).isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def _lock(conn, key):
    return conn.execute("SELECT * FROM exec_locks WHERE key=?", (key,)).fetchone()


def _reserve(store, key, amount="1000", *, ticker="000660", marks=None, now=NOW):
    return store.claim_reserved(
        key, now=now, account_id="a", market="KR", ticker=ticker, side="BUY",
        marks=marks or {"order_id": 0, "approval_id": 0, "at": ""},
        recheck=lambda inflight: (True, Decimal(amount)),
    )  # fmt: skip


@pytest.fixture
def store(conn):
    return ExecKeyStore(conn)


def test_claim_is_exclusive(store):
    assert store.claim("k", now=NOW)
    assert not store.claim("k", now=NOW)
    assert store.claim("other", now=NOW)


def test_dead_order_key_is_reclaimed(conn, store):
    assert store.claim("k", now=NOW)
    oid = _order(conn, "accepted")
    store.bind("k", order_id=oid)
    assert not store.claim("k", now=NOW)  # 살아 있는 주문
    for status in ("rejected", "cancelled", "failed", "expired"):
        conn.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
        conn.commit()
        assert store.claim("k", now=NOW), status
        row = _lock(conn, "k")
        assert row["order_id"] is None and row["sent_at"] is None
        store.bind("k", order_id=oid)
    conn.execute("UPDATE orders SET status='filled' WHERE id=?", (oid,))
    conn.commit()
    assert not store.claim("k", now=NOW)  # 체결 완료는 죽은 주문이 아니다


def test_stale_unsent_key_is_reclaimed_but_sent_key_is_not(conn, store):
    old = (T0 - timedelta(minutes=11)).isoformat()
    conn.execute("INSERT INTO exec_locks(key, created_at, sent_at) VALUES ('k', ?, ?)", (old, old))
    conn.commit()
    assert not store.claim("k", now=NOW)  # 보냈는데 주문 행이 없는 키: 수동 대조
    conn.execute("UPDATE exec_locks SET sent_at=NULL WHERE key='k'")
    conn.commit()
    assert store.claim("k", now=NOW)  # 선점 뒤 죽은 프로세스의 키
    assert not store.claim("k", now=NOW)  # 방금 넘겨받아 신선하다
    fresh = (T0 - timedelta(minutes=9)).isoformat()
    conn.execute("INSERT INTO exec_locks(key, created_at) VALUES ('f', ?)", (fresh,))
    conn.commit()
    assert not store.claim("f", now=NOW)  # 10분 안의 미발송 키는 아직 살아 있다


def test_closed_approval_key_is_reclaimed_pending_blocks(conn, store):
    assert store.claim("k", now=NOW)
    aid = _approval(conn, "pending")
    store.bind("k", approval_id=aid)
    assert not store.claim("k", now=NOW)  # 살아 있는 pending
    for status in ("rejected", "expired", "failed"):
        conn.execute("UPDATE approvals SET status=? WHERE id=?", (status, aid))
        conn.commit()
        assert store.claim("k", now=NOW), status
        store.bind("k", approval_id=aid)
    conn.execute("UPDATE approvals SET status='approved' WHERE id=?", (aid,))
    conn.commit()
    assert not store.claim("k", now=NOW)
    # 만료 시각이 지났는데 아직 pending 인 요청은 닫힌 것으로 본다
    conn.execute(
        "UPDATE approvals SET status='pending', expires_at=? WHERE id=?",
        ((T0 - timedelta(seconds=1)).isoformat(), aid),
    )
    conn.commit()
    assert store.claim("k", now=NOW)
    # 보냈으면 승인이 닫혀도 넘겨받지 않는다
    store.bind("k", approval_id=aid)
    store.mark_sent("k", sent_at=NOW)
    assert not store.claim("k", now=NOW)


def test_claim_reserved_runs_recheck_with_inflight_inside_write_lock(conn, store):
    assert _reserve(store, "other|k", "9000", ticker="000660")[0]
    seen: dict = {}

    def recheck(inflight):
        seen["inflight"] = inflight
        seen["tx"] = conn.in_transaction
        return True, Decimal("500")

    claimed, inflight = store.claim_reserved(
        "mine", now=NOW, account_id="a", market="KR", ticker="005930", side="BUY",
        marks={"order_id": 0, "approval_id": 0, "at": ""}, recheck=recheck,
    )  # fmt: skip
    assert claimed and seen["tx"]
    assert inflight == seen["inflight"]
    assert [(r["key"], r["ticker"], r["amount"]) for r in inflight] == [
        ("other|k", "000660", "9000")
    ]
    r = conn.execute("SELECT * FROM buy_reservations WHERE key='mine'").fetchone()
    assert (r["account_id"], r["market"], r["ticker"], r["side"], r["amount"]) == (
        "a", "KR", "005930", "BUY", "500",
    )  # fmt: skip
    assert not conn.in_transaction
    # recheck 거부면 잡지 않는다
    claimed, _ = store.claim_reserved(
        "no", now=NOW, account_id="a", market="KR", ticker="005930", side="BUY",
        marks={"order_id": 0, "approval_id": 0, "at": ""}, recheck=lambda i: (False, None),
    )  # fmt: skip
    assert not claimed and _lock(conn, "no") is None and not conn.in_transaction
    # 살아 있는 키면 잡지 못하고 기존 예약도 그대로
    assert not _reserve(store, "mine", "1")[0]
    assert (
        conn.execute("SELECT amount FROM buy_reservations WHERE key='mine'").fetchone()[0] == "500"
    )
    # 다른 계좌·시장 예약은 보지 않는다
    assert store.inflight(account_id="b", market="KR", marks={}) == []


def test_reservation_bound_order_visible_in_marks_is_excluded(conn, store):
    assert _reserve(store, "o1", "100")[0]
    assert _reserve(store, "o2", "200", ticker="000270")[0]
    oid = _order(conn, "accepted")
    store.bind("o1", order_id=oid)
    base = {"order_id": 0, "approval_id": 0, "at": ""}

    def keys(m):
        found = store.inflight(account_id="a", market="KR", marks=m, now=NOW)
        return {r["key"] for r in found}

    assert keys({**base, "order_id": oid - 1}) == {"o1", "o2"}  # 표시 뒤에 생긴 주문 = 진행 중
    assert keys({**base, "order_id": oid}) == {"o2"}  # 이미 센 주문
    conn.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
    conn.commit()
    assert keys(base) == {"o2"}  # 죽은 주문은 예약이 아니다
    # 표시 전에 만든 pending 승인: 아직 pending 이면 이미 센 것
    aid = _approval(conn)
    store.bind("o2", approval_id=aid)
    assert keys({**base, "approval_id": aid}) == set()
    assert keys({**base, "approval_id": aid - 1}) == {"o2"}
    # 보내지도 묶이지도 않은 채 오래된 예약은 무시
    old = (T0 - timedelta(hours=1)).isoformat()
    assert _reserve(store, "old", "5", ticker="1", now=old)[0]
    conn.execute("UPDATE exec_locks SET created_at=? WHERE key='old'", (old,))
    conn.commit()
    assert "old" not in keys(base)


def test_release_and_has_approval(conn, store):
    assert _reserve(store, "k")[0]
    assert not store.has_approval("k") and not store.has_approval("none")
    store.bind("k", approval_id=_approval(conn))
    assert store.has_approval("k")
    assert conn.execute("SELECT bound_at FROM buy_reservations WHERE key='k'").fetchone()[0]
    store.release("k")
    assert _lock(conn, "k") is None
    assert conn.execute("SELECT COUNT(*) FROM buy_reservations").fetchone()[0] == 0
    assert store.claim("k", now=NOW)  # 풀리면 다시 잡을 수 있다


def test_list_locks(conn, store):
    assert store.list_all() == []
    store.claim("b", now=(T0 + timedelta(seconds=1)).isoformat())
    store.claim("a", now=NOW)
    store.mark_sent("a", sent_at=NOW)
    rows = store.list_all()
    assert [r["key"] for r in rows] == ["a", "b"]  # 오래된 순
    assert rows[0]["sent_at"] == NOW and rows[0]["order_id"] is None
    assert set(rows[0]) == {"key", "created_at", "order_id", "approval_id", "sent_at"}


def test_claim_reserved_takeover_replaces_old_reservation(conn, store):
    assert _reserve(store, "k", "100")[0]
    oid = _order(conn, "accepted")
    store.bind("k", order_id=oid)
    claimed, _ = _reserve(store, "k", "700")  # 살아 있는 주문 -> 넘겨받기 실패, 옛 예약 유지
    assert not claimed
    row = conn.execute("SELECT amount, bound_at FROM buy_reservations WHERE key='k'").fetchone()
    assert row["amount"] == "100" and row["bound_at"] is not None
    assert _lock(conn, "k")["order_id"] == oid
    conn.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
    conn.commit()
    assert _reserve(store, "k", "700")[0]  # 죽은 주문 -> 넘겨받기
    rows = conn.execute("SELECT amount, bound_at FROM buy_reservations WHERE key='k'").fetchall()
    assert [(r["amount"], r["bound_at"]) for r in rows] == [("700", None)]
    assert _lock(conn, "k")["order_id"] is None


def test_claim_commits_pending_caller_writes(conn, store):
    assert store.claim("k", now=NOW)
    _order(conn)  # commit 됨
    conn.execute(
        "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, status, mode, "
        "created_at) VALUES ('a','X','KR','BUY',1,'LIMIT','accepted','auto',?)",
        (NOW,),
    )  # 미커밋 쓰기
    assert not store.claim("k", now=NOW)  # IntegrityError 경로
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM orders WHERE ticker='X'").fetchone()[0] == 1


def test_has_approval_sees_pending_approval_row_before_bind(conn, store):
    """승인 행은 생겼는데 `bind` 전에 실패했어도(approval_id 없음) 키는 승인에 걸린 것으로 본다 —
    호출자가 키를 풀어 pending 승인과 새 선점이 함께 살지 않게."""
    assert store.claim("k", now=NOW)
    aid = _approval(conn)
    conn.execute("UPDATE approvals SET exec_key='k' WHERE id=?", (aid,))
    conn.commit()
    assert _lock(conn, "k")["approval_id"] is None
    assert store.has_approval("k")
    conn.execute("UPDATE approvals SET status='rejected' WHERE id=?", (aid,))
    conn.commit()
    assert not store.has_approval("k")  # 닫힌 승인은 키를 붙잡지 않는다
