"""Phase 26 단계 2 Task 2.3: 상태 전이표·재시작 복구·adopt·차단 상태."""

from __future__ import annotations

from decimal import Decimal

from tests.features.fo._support import ACCT, NOW, _bal, _open, _recover, _row, _state
from trader.features.fo import positions, store
from trader.kernel.broker.models import FoFill, UnverifiedHolding


def test_transitions_table_rejects_illegal():
    assert positions.can_transition("entering", "held")
    assert positions.can_transition("held", "closing")
    assert positions.can_transition("closing", "exit_failed")
    assert positions.can_transition("awaiting_settlement", "settled")
    assert not positions.can_transition("closed", "held")
    assert not positions.can_transition("settled", "closing")
    assert not positions.can_transition("unknown", "closing")  # unknown 은 사람만(adopt/close)
    assert not positions.can_transition("held", "entering")
    assert set(positions.TRANSITIONS) == set(positions.TRANSITIONS.keys()) and all(
        s in positions.TRANSITIONS for v in positions.TRANSITIONS.values() for s in v
    )


def test_recovery_matching_inherits_stop_and_mult(conn):
    pos, leg = _open(conn)
    store.set_leg(conn, leg.id, status="accepted", order_no="A1")
    store.record_fill_progress(conn, leg.id, 2)
    store.transition(conn, pos.id, expected_version=store.get_position(conn, pos.id).version,
                     status="held")  # fmt: skip
    rep = _recover(conn, _bal(_row(qty=2)))
    assert rep.adopted == [pos.id] and rep.unknown == [] and rep.alerts == []
    after = store.get_position(conn, pos.id)
    assert (after.status, after.stop_pt, after.approved_mult) == ("held", Decimal("345"), 1.5)
    assert positions.has_blocking_state(conn, ACCT) is None


def test_recovery_unverified_blocks_new_and_never_orders(conn):
    """미확정 잔고 행은 포지션 행을 만들지 않고(자동 주문 대상 아님) 신규를 막는 사유가 된다."""
    unv = UnverifiedHolding(code="105W11", raw={"cblc_qty": "?"}, reason="수량 해석 불가")
    rep = _recover(conn, _bal(unverified=[unv]))
    assert rep.unverified == ["105W11: 수량 해석 불가"] and rep.unknown == []
    assert store.open_positions(conn, ACCT) == []
    assert any("[파생 잔고 미확정]" in a for a in rep.alerts)
    assert (
        positions.has_blocking_state(conn, ACCT, unverified=rep.unverified) == "미확정 잔고 행 1건"
    )


def test_recovery_broker_only_holding_becomes_unknown_row(conn):
    """잔고에만 있는 확정 보유 → unknown 행 + 알림, adopt 전엔 차단."""
    rep = _recover(conn, _bal(_row(code="305W11350", kind="PUT", side="SHORT", qty=1)))
    assert len(rep.unknown) == 1
    pos = store.get_position(conn, rep.unknown[0])
    assert (pos.status, pos.kind, pos.contract_side, pos.qty_held, pos.vehicle, pos.direction) == (
        "unknown", "PUT", "SHORT", 1, "OPTION_SELL", "LONG",
    )  # fmt: skip
    assert any("[파생 잔고 출처 불명]" in a for a in rep.alerts)
    assert positions.has_blocking_state(conn, ACCT) == "unknown 포지션 1건"
    # 두 번 복구해도 행이 늘지 않는다
    rep2 = _recover(conn, _bal(_row(code="305W11350", kind="PUT", side="SHORT", qty=1)))
    assert rep2.unknown == [] and len(store.open_positions(conn, ACCT)) == 1


def test_adopt_requires_broker_match(conn):
    rep = _recover(conn, _bal(_row(qty=2)))
    pid = rep.unknown[0]
    bal = _bal(_row(qty=2))
    assert positions.adopt(conn, position_id=pid, side="SHORT", kind="FUTURES", qty=2,
                           stop_pt=Decimal("355"), balance=bal) is False  # fmt: skip
    assert positions.adopt(conn, position_id=pid, side="LONG", kind="FUTURES", qty=3,
                           stop_pt=Decimal("345"), balance=bal) is False  # fmt: skip
    assert store.get_position(conn, pid).status == "unknown"
    assert positions.adopt(conn, position_id=pid, side="LONG", kind="FUTURES", qty=2,
                           stop_pt=Decimal("345"), balance=bal) is True  # fmt: skip
    pos = store.get_position(conn, pid)
    assert (pos.status, pos.stop_pt, pos.qty_held) == ("held", Decimal("345"), 2)
    assert positions.has_blocking_state(conn, ACCT) is None
    assert positions.adopt(conn, position_id=pid, side="LONG", kind="FUTURES", qty=2,
                           stop_pt=Decimal("345"), balance=bal) is False  # 이미 held  # fmt: skip


def test_recovery_qty_mismatch_unknown(conn):
    """원장 2 > 브로커 1 인데 살아 있는 축소 다리가 없으면 unknown."""
    pos, leg = _open(conn)
    store.set_leg(conn, leg.id, status="accepted", order_no="A1")
    store.record_fill_progress(conn, leg.id, 2)
    rep = _recover(conn, _bal(_row(qty=1)))
    assert rep.unknown == [pos.id]
    assert store.get_position(conn, pos.id).status == "unknown"
    assert positions.has_blocking_state(conn, ACCT) == "unknown 포지션 1건"


def test_recovery_qty_mismatch_explained_by_live_leg(conn):
    """브로커 2 > 원장 1 이고 진입 다리가 살아 있으면(부분체결 미반영) 승계."""
    pos, leg = _open(conn, qty=3)
    store.set_leg(conn, leg.id, status="accepted", order_no="A1")
    store.record_fill_progress(conn, leg.id, 1)
    rep = _recover(conn, _bal(_row(qty=2)))
    assert rep.adopted == [pos.id] and rep.unknown == []


def test_recovery_ledger_only_after_last_trade_date_is_awaiting_settlement(conn):
    pos, leg = _open(conn, last_trade_date="2026-10-22")
    store.set_leg(conn, leg.id, status="filled", order_no="A1")
    store.record_fill_progress(conn, leg.id, 2)
    p = store.get_position(conn, pos.id)
    store.transition(conn, p.id, expected_version=p.version, status="held")
    rep = _recover(conn, _bal())  # 잔고에서 사라짐
    assert rep.settling == [pos.id]
    assert store.get_position(conn, pos.id).status == "awaiting_settlement"
    # 만기 전인데 사라졌고 체결 설명이 없으면 unknown
    pos2, leg2 = _open(conn, code="105W12", last_trade_date="2026-12-10", max_positions=2)
    store.record_fill_progress(conn, leg2.id, 2)
    store.set_leg(conn, leg2.id, status="filled", order_no="A2")
    rep2 = _recover(conn, _bal())
    assert pos2.id in rep2.unknown


def test_recovery_entering_without_fill_is_kept(conn):
    pos, leg = _open(conn)
    store.set_leg(conn, leg.id, status="accepted", order_no="A1")
    rep = _recover(conn, _bal())
    assert rep.adopted == [pos.id] and store.get_position(conn, pos.id).status == "entering"


def test_sending_leg_confirmed_by_order_state(conn):
    """sending 다리(주문번호 있음)는 체결 조회로 확정한다 — 재전송 없음."""
    pos, leg = _open(conn)
    store.set_leg(conn, leg.id, status="sending", order_no="A1", sent_at=NOW.isoformat())
    rep = _recover(conn, _bal(_row(qty=2)), states={"A1": _state("A1", order_qty=2, filled=2)})
    assert rep.legs_fixed == 1
    after = store.get_leg(conn, leg.id)
    assert (after.status, after.filled_qty) == ("filled", 2)
    assert store.get_position(conn, pos.id).qty_held == 2
    assert pos.id in rep.adopted


def test_sending_leg_without_order_no_matches_exactly_one_fill(conn):
    pos, leg = _open(conn)
    store.set_leg(conn, leg.id, status="sending", sent_at=NOW.isoformat())
    fill = FoFill(
        order_no="B7", orgn_order_no=None, code="105W11", side="BUY", remaining_qty=0,
        filled_qty=2, rejected_qty=0, cancelled_qty=0, filled_amount=Decimal("35050000"),
        avg_price=Decimal("350.5"), filled_at=NOW,
    )  # fmt: skip
    # 후보 2건이면 묶지 않는다 → unknown
    two = [fill, fill.model_copy(update={"order_no": "B8"})]
    rep = _recover(conn, _bal(_row(qty=2)), fills=two,
                   states={"B7": _state("B7", order_qty=2, filled=2)})  # fmt: skip
    assert rep.legs_fixed == 0 and pos.id in rep.unknown
    # 사람이 unknown 을 풀기 전엔 그대로(unknown 은 사람만)
    store.transition(conn, pos.id, expected_version=store.get_position(conn, pos.id).version,
                     status="held")  # fmt: skip
    rep2 = _recover(conn, _bal(_row(qty=2)), fills=[fill],
                    states={"B7": _state("B7", order_qty=2, filled=2)})  # fmt: skip
    assert rep2.legs_fixed == 1 and store.get_leg(conn, leg.id).order_no == "B7"


def test_unknown_leg_query_failure_keeps_unknown(conn):
    """조회 실패는 다리를 unknown 으로 두고 보류(`deferred`)한다 — 포지션 unknown 격상은 아니다
    (단계 2 리뷰 C2-5). 신규는 미확정 다리로 막힌다."""
    pos, leg = _open(conn)
    store.set_leg(conn, leg.id, status="unknown", order_no="A1")
    rep = _recover(conn, _bal(), raise_state=True)
    assert store.get_leg(conn, leg.id).status == "unknown" and pos.id not in rep.unknown
    assert rep.deferred == [leg.id]
    assert positions.has_blocking_state(conn, ACCT) == "미확정 다리 1건"
