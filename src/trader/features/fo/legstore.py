"""fo 다리 멱등 저장소 `LegStore` — 커널 `IdemStore` Protocol 의 fo 구현(M10 A3).

FO 의 실행 키는 `fo_order_legs` 행 자체라 키는 `f"leg:{leg_id}"` 다. 선점은 `store.claim_leg` 의
CAS(`prepared→sending`), 해제는 `store.cancel_prepared`(prepared 만) 다. 이 모듈은 전송하지
않는다 — 실행기 `_transmit` 이 `claim`→(전송)→`bind`/`release` 로 부른다. 현금 예약·승인 개념이
없어 `claim_reserved`·`has_approval` 은 Protocol 충족용이다."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from decimal import Decimal

from trader.features.fo import store
from trader.features.fo.types import FoLegRow

_PREFIX = "leg:"


def leg_key(leg_id: int) -> str:
    return f"{_PREFIX}{leg_id}"


def leg_id_of(key: str) -> int:
    """`"leg:<id>"` 키에서 다리 id 를 뽑는다. 그 밖의 형식은 `ValueError`."""
    if not key.startswith(_PREFIX) or not key[len(_PREFIX) :].isdecimal():
        raise ValueError(f"fo 다리 키가 아님: {key!r}")
    return int(key[len(_PREFIX) :])


class LegStore:
    """`IdemStore` 의 fo 구현. 각 메서드는 `store.tx` 로 쓴다 — 단독으로 부르면 자기 경계(짧은
    쓰기 뒤 commit, `ExecKeyStore` 와 같다), `store.tx` 안에서 부르면 합류한다(실행기 `_transmit`
    이 orders 행 INSERT 와 `bind` 를 한 트랜잭션으로 묶는다, A1)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def claim(self, key: str, *, now: str) -> bool:
        """`prepared` 다리만 `sending`(sent_at=now)으로 선점한다. 이미 선점·회수됐으면 False."""
        with store.tx(self.conn):
            return store.claim_leg(self.conn, leg_id_of(key), sent_at=now)

    def claim_reserved(
        self,
        key: str,
        *,
        now: str,
        account_id: str,
        market: str,
        ticker: str,
        side: str,
        marks: dict,
        recheck: Callable[[list[dict]], tuple[bool, Decimal | None]],
    ) -> tuple[bool, list[dict]]:
        """Protocol 충족용 — 현금 예약 개념이 없다(증거금은 게이트가 본다). `claim` 뒤 빈 진행 중
        목록으로 `recheck` 를 부르고, 거부되면 선점을 되돌린다."""
        if not self.claim(key, now=now):
            return False, []
        ok, _amount = recheck([])
        if not ok:
            with store.tx(self.conn):
                self.conn.execute(
                    "UPDATE fo_order_legs SET status = 'prepared', sent_at = NULL "
                    "WHERE id = ? AND status = 'sending'",
                    (leg_id_of(key),),
                )
            return False, []
        return True, []

    def bind(
        self, key: str, *, order_id: int | None = None, approval_id: int | None = None,
        at: str | None = None,
    ) -> None:  # fmt: skip
        """다리를 주문 행에 묶는다(`order_id`, 접수 시각 `at`). 승인은 없다(`approval_id` 무시)."""
        with store.tx(self.conn):
            store.set_leg(self.conn, leg_id_of(key), order_id=order_id, accepted_at=at)

    def mark_sent(self, key: str, *, sent_at: str) -> None:
        with store.tx(self.conn):
            store.set_leg(self.conn, leg_id_of(key), sent_at=sent_at)

    def release(self, key: str) -> None:
        """`prepared` 다리만 cancelled. 이미 보낸(sending 이후) 다리는 그대로(no-op)."""
        with store.tx(self.conn):
            store.cancel_prepared(self.conn, leg_id_of(key))

    def has_approval(self, key: str) -> bool:
        """Protocol 충족용 — fo 에는 승인이 없다."""
        return False

    def inflight(self, *, account_id: str, market: str, marks: dict) -> list[dict]:
        """살아 있는 다리(prepared·sending·accepted·unknown) 를 dict 로."""
        return [_leg_dict(x) for x in store.live_legs(self.conn, account_id=account_id)]

    def list_all(self) -> list[dict]:
        return [_leg_dict(x) for x in store.live_legs(self.conn)]


def _leg_dict(leg: FoLegRow) -> dict:
    return {
        "key": leg_key(leg.id),
        "position_id": leg.position_id,
        "account_id": leg.account_id,
        "intent": leg.intent,
        "status": leg.status,
        "qty": leg.qty,
        "order_id": leg.order_id,
        "sent_at": leg.sent_at,
    }
