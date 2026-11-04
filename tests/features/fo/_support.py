"""fo 테스트 공용 헬퍼(옛 `tests/fo/test_positions.py` 의 결합 헬퍼 1차 분리). 복구·실행기·엔진
테스트가 import 한다: `_bal`(잔고)·`_row`(브로커 보유 행)·`_state`(주문 상태)·`_open`(진입 다리
예약)·`_recover`(전송 유예가 지난 시점의 복구)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from tests.features.fo.conftest import ACCT, NOW, TODAY
from trader.features.fo import positions, store
from trader.features.fo.store import ReservePlan
from trader.kernel.broker.models import FoBalance, FoCash, FoOrderState, FoPosition

__all__ = ["ACCT", "NOW", "TODAY", "_bal", "_open", "_recover", "_row", "_state"]


def _cash() -> FoCash:
    return FoCash(
        deposit=Decimal("1"), orderable_cash=Decimal("1"), orderable_total=Decimal("1"),
        margin_total=Decimal("0"), add_margin=Decimal("0"), nav=Decimal("10000000"),
        realized_today=Decimal("0"), unrealized=Decimal("0"), requested_at=NOW, received_at=NOW,
    )  # fmt: skip


def _bal(*rows: FoPosition, unverified=()) -> FoBalance:
    return FoBalance(positions=list(rows), unverified=list(unverified), cash=_cash())


def _row(code="105W11", kind="FUTURES", side="LONG", qty=2) -> FoPosition:
    return FoPosition(
        code=code, name=None, kind=kind, contract_side=side, qty=qty, avg_price=Decimal("350"),
        current_price=Decimal("351"), pnl=Decimal("0"), sellable_qty=qty,
    )  # fmt: skip


def _state(order_no, *, order_qty, filled, remaining=0, rejected=0, cancelled=0, state="filled"):
    terminal = (remaining == 0 or state in ("cancelled", "rejected")) and (
        filled + rejected + cancelled == order_qty
    )
    return FoOrderState(
        order_no=order_no, order_qty=order_qty, filled_qty=filled, remaining_qty=remaining,
        rejected_qty=rejected, cancelled_qty=cancelled, terminal=terminal, state=state,
    )  # fmt: skip


def _open(conn, qty=2, *, status="entering", max_positions=1, **over):
    """진입 다리를 예약하고 (position, leg) 를 돌려준다."""
    with store.tx(conn):
        r = store.reserve(
            conn,
            ACCT,
            ReservePlan(
                intent="OPEN", round=0, qty=qty, price=Decimal("350.5"), order_type="LIMIT",
                new_position={
                    "account_id": ACCT, "code": "105W11", "kind": "FUTURES",
                    "contract_side": "LONG", "direction": "LONG", "vehicle": "FUTURES",
                    "nav_at_open": Decimal("10000000"), "stop_pt": Decimal("345"),
                    "approved_mult": 1.5, "leverage_used": 1.2, **over,
                },
                new_status=status,
            ),
            max_positions=max_positions,
            max_contracts=4,
        )  # fmt: skip
    assert r.ok, r.reasons
    return store.get_position(conn, r.position_id), store.get_leg(conn, r.leg_id)


def _recover(conn, balance, *, states=None, fills=None, raise_state=False):
    conn.commit()  # 테스트가 연 암묵 트랜잭션을 닫는다 — `store.tx` 는 바깥 트랜잭션에 합류만 한다

    def order_state_of(no, d):
        if raise_state:
            raise RuntimeError("조회 실패")
        return (states or {}).get(no) or _state(no, order_qty=0, filled=0, state="open")

    return positions.recover(
        conn,
        account_id=ACCT,
        balance=balance,
        order_state_of=order_state_of,
        fills_of=lambda d: list(fills or []),
        today=TODAY,
        now=NOW + timedelta(minutes=5),  # 전송 뒤 유예(SENDING_GRACE_S)가 지난 복구
    )
