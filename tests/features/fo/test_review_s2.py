"""Phase 26 단계 2 리뷰 반영(Codex S2-1~12·review R-1~4, Claude C2-1~10).

저장 계층: 트랜잭션 안 commit 금지(S2-1)·prepared 선점(S2-2)·상태 전이표 검증(C2-10)·예약 재검사가
보유·미체결 계획 손실을 받음(S2-7). 게이트: 대상 포지션 상태(C2-4)·감액 RESIZE 는 진입 조건
무관(S2-6)·보유 계획 손실 평균가(S2-7)·로컬 예약 차감(S2-8·C2-9)·저장 배수 단조(S2-9)·주문가능
조회는 호가단위 가격(C2-8). 실행기: HOLD 손절 강화 시 stop_fut(S2-10)·결정 RESIZE_DOWN 도 회수
→확정 경로(S2-5)·만료된 축소 다리 재발행(S2-4)·잔고 덮어쓰기 대신 체결 반영(S2-11·C2-1)·회수
선반영 체결의 경제값 1회(S2-3)·빈 entering 정리(C2-2)·잠금 밖 조회(S2-12)·조회 실패는 보류(C2-5).

Task 3 은 저장 계층·복구(positions) 건만 싣는다. 게이트(rules)·실행기(executor) 건은 Task 4~5 에서
이 파일에 이어 붙인다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.features.fo._support import ACCT, NOW, TODAY, _bal, _open, _recover, _row, _state
from trader.features.fo import positions, store
from trader.features.fo.store import ReservePlan
from trader.kernel.execution import ledger


def _held_position(conn, qty=2, *, status="held"):
    """체결까지 반영된 held(또는 `status`) 포지션 — 옛 test_executor `_held_position` 의 저장
    계층 부분."""
    pos, leg = _open(conn, qty=qty)
    store.set_leg(conn, leg.id, status="filled", order_no="A1")
    store.record_fill_progress(conn, leg.id, qty)
    cur = store.get_position(conn, pos.id)
    assert store.transition(conn, pos.id, expected_version=cur.version, status="held")
    if status != "held":
        cur = store.get_position(conn, pos.id)
        assert store.transition(conn, pos.id, expected_version=cur.version, status=status)
    conn.commit()
    return store.get_position(conn, pos.id)


# --- 저장 계층 ---------------------------------------------------------------------------


def test_repo_insert_helpers_can_skip_commit(conn):
    """체결 동기화가 한 트랜잭션 안에서 부르는 `insert_order(commit=False)` 는 원자성을 지킨다
    (S2-1). 커널 `insert_fill` 은 항상 commit 하므로 주문 쪽만 확인한다."""
    with pytest.raises(RuntimeError), store.tx(conn):
        ledger.insert_order(
            conn, decision_id=None, account_id=ACCT, ticker="105W11", market="KR", side="BUY",
            qty=1, order_type="LIMIT", price=Decimal("350.5"), status="accepted", order_no="X",
            message="", mode="auto", asset="fo", commit=False,
        )  # fmt: skip
        assert conn.in_transaction
        raise RuntimeError("중단")
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_transition_validates_state_table(conn):
    """상태 전이는 `TRANSITIONS` 표 밖이면 ValueError, 예약은 사유로 거부(C2-10)."""
    pos = _held_position(conn, qty=2, status="closing")
    with pytest.raises(ValueError):
        store.transition(conn, pos.id, expected_version=pos.version, status="resizing")
    assert store.get_position(conn, pos.id).status == "closing"
    with store.tx(conn):
        r = store.reserve(
            conn, ACCT,
            ReservePlan(intent="RESIZE_UP", round=0, qty=1, price=None, order_type="MARKET",
                        position_id=pos.id, expected_version=pos.version, new_status="resizing"),
            max_positions=1, max_contracts=4,
        )  # fmt: skip
    assert not r.ok and any("전이 불가" in s for s in r.reasons)


def test_claim_leg_only_from_prepared(conn):
    """회수가 cancelled 로 바꾼 prepared 다리는 선점되지 않는다(S2-2; 실행기 `_transmit` 쪽은
    Task 5)."""
    pos = _held_position(conn, qty=2)
    with store.tx(conn):
        lid = store.insert_leg(conn, position_id=pos.id, account_id=ACCT, intent="RESIZE_UP",
                               round=0, attempt=1, qty=1, price=Decimal("350.5"),
                               order_type="LIMIT")  # fmt: skip
        store.set_leg(conn, lid, status="cancelled")
    assert store.claim_leg(conn, lid) is False
    assert store.get_leg(conn, lid).status == "cancelled"


def test_reserve_budget_uses_existing_planned_loss(conn):
    """예약 재검사: 보유·미체결 계획 손실(사이징이 평균가로 계산) + 이번 다리 × 단가 ≤ 한도
    (S2-7)."""
    pos = _held_position(conn, qty=2)
    with store.tx(conn):
        r = store.reserve(
            conn, ACCT,
            ReservePlan(intent="RESIZE_UP", round=0, qty=1, price=None, order_type="MARKET",
                        position_id=pos.id, expected_version=pos.version, new_status="resizing",
                        planned_loss_per_contract=Decimal("105000"),
                        planned_loss_existing=Decimal("960000"),
                        budget_limit=Decimal("1000000")),
            max_positions=1, max_contracts=4,
        )  # fmt: skip
    assert not r.ok and any("수명 손실 예산" in s for s in r.reasons)


def test_tx_joins_outer_transaction(conn):
    """이미 열린 트랜잭션 안에서는 합류한다 — commit·rollback 은 바깥 몫(옛 구현은 먼저 commit)."""
    conn.execute("BEGIN")
    store.insert_position(conn, **{
        "account_id": ACCT, "code": "105W11", "kind": "FUTURES", "contract_side": "LONG",
        "direction": "LONG", "vehicle": "FUTURES", "status": "entering",
    })  # fmt: skip
    with store.tx(conn):
        assert conn.in_transaction
    assert conn.in_transaction  # 합류한 tx 는 commit 하지 않는다
    conn.rollback()
    assert store.open_positions(conn, ACCT) == []


# --- 복구 -------------------------------------------------------------------------------


def test_recover_query_failure_defers_instead_of_unknown(conn):
    """주문 상태 조회 실패(네트워크)는 미확정 유지·보류이지 unknown 격상이 아니다(C2-5)."""
    pos, leg = _open(conn)
    store.set_leg(conn, leg.id, status="unknown", order_no="A1")
    rep = _recover(conn, _bal(), raise_state=True)
    assert rep.unknown == [] and rep.deferred == [leg.id]
    assert store.get_leg(conn, leg.id).status == "unknown"
    assert store.get_position(conn, pos.id).status == "entering"
    assert positions.has_blocking_state(conn, ACCT) == "미확정 다리 1건"


def test_cancelled_entry_leg_closes_empty_entering(conn):
    pos, leg = _open(conn)
    store.set_leg(conn, leg.id, status="sending", order_no="A1", sent_at=NOW.isoformat())
    _recover(conn, _bal(), states={"A1": _state("A1", order_qty=2, filled=0, cancelled=2,
                                                 state="cancelled")})  # fmt: skip
    assert store.get_position(conn, pos.id).status == "closed"
    assert store.open_positions(conn, ACCT) == []


def test_recover_releases_lock_between_broker_queries(conn):
    """복구 ①의 다리 확정은 앞 다리의 쓰기를 commit 한 뒤 다음 조회를 한다(S2-12)."""
    pos, leg = _open(conn)
    with store.tx(conn):
        store.set_leg(conn, leg.id, status="unknown", order_no="A1")
        lid2 = store.insert_leg(conn, position_id=pos.id, account_id=ACCT, intent="OPEN",
                                round=0, attempt=2, qty=1, price=Decimal("350.5"),
                                order_type="LIMIT")  # fmt: skip
        store.set_leg(conn, lid2, status="unknown", order_no="A2")
    flags: list[bool] = []

    def order_state_of(no, d):
        flags.append(conn.in_transaction)
        return _state(no, order_qty=2 if no == "A1" else 1, filled=2 if no == "A1" else 1)

    rep = positions.recover(conn, account_id=ACCT, balance=_bal(_row(qty=3)),
                            order_state_of=order_state_of, fills_of=lambda d: [], today=TODAY,
                            now=NOW)  # fmt: skip
    assert flags == [False, False] and rep.legs_fixed == 2
