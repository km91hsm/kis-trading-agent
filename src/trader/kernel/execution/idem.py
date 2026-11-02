"""실행 키(멱등 선점) 저장소: 두 프로세스가 같은 주문을 동시에 내지 않게 한다.

`exec_locks` 가 키를 쥐고, 매수 예약(`claim_reserved`)이면 `buy_reservations` 가 계좌·시장·종목·
금액을 함께 남겨 다른 경로의 동시 판정이 서로의 진행 중 예약을 보게 한다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from trader.kernel.execution.types import EXEC_KEY_STALE, dead_status_sql
from trader.kernel.ledger.db import now_iso

_CLOSED_APPROVALS = "('rejected','expired','failed')"


class IdemStore(Protocol):
    """멱등 키 저장소 계약(코어는 이 Protocol 만 안다)."""

    def claim(self, key: str, *, now: str) -> bool: ...

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
    ) -> tuple[bool, list[dict]]: ...

    def bind(
        self, key: str, *, order_id: int | None = None, approval_id: int | None = None,
        at: str | None = None,
    ) -> None: ...  # fmt: skip

    def mark_sent(self, key: str, *, sent_at: str) -> None: ...

    def release(self, key: str) -> None: ...

    def has_approval(self, key: str) -> bool: ...

    def inflight(self, *, account_id: str, market: str, marks: dict) -> list[dict]: ...

    def list_all(self) -> list[dict]: ...


class ExecKeyStore:
    """`IdemStore` 의 SQLite 구현. 호출마다 commit 한다(짧은 쓰기)."""

    def __init__(self, conn: sqlite3.Connection, *, stale: timedelta = EXEC_KEY_STALE) -> None:
        self.conn = conn
        self.stale = stale

    def _stale_before(self, now: str) -> str:
        return (datetime.fromisoformat(now) - self.stale).isoformat()

    # 넘겨받기 조건(인벤토리 「Claim rule」): 묶인 주문이 죽었다, 또는 보내지 않았고(sent_at 없음)
    # 주문도 없으며 (승인 없이 오래됐다 | 승인이 닫혔다 | pending 인데 만료 시각이 지났다).
    # 보냈는데 주문 행이 없는 키(접수 뒤 기록 실패)는 절대 넘겨받지 않는다 — 수동 대조 대상.
    _TAKEOVER = (
        "(order_id IS NOT NULL AND order_id IN (SELECT id FROM orders WHERE status IN ({dead}))) "
        "OR (order_id IS NULL AND sent_at IS NULL AND ("
        "(approval_id IS NULL AND created_at < ?) "
        "OR approval_id IN (SELECT id FROM approvals WHERE status IN " + _CLOSED_APPROVALS +
        " OR (status='pending' AND expires_at <= ?))))"
    )  # fmt: skip

    def _takeover(self, key: str, now: str) -> bool:
        """이미 있는 키를 조건부 UPDATE 한 번으로 넘겨받는다. 옛 예약 행은 지운다."""
        cond = self._TAKEOVER.format(dead=dead_status_sql())
        cur = self.conn.execute(
            "UPDATE exec_locks SET created_at=?, order_id=NULL, approval_id=NULL, sent_at=NULL "
            f"WHERE key=? AND ({cond})",
            (now, key, self._stale_before(now), now),
        )
        if cur.rowcount != 1:
            return False
        self.conn.execute("DELETE FROM buy_reservations WHERE key=?", (key,))
        return True

    def claim(self, key: str, *, now: str) -> bool:
        """처음 넣은 프로세스만 True. 살아 있는 키가 있으면 False(넘겨받기 규칙은 `_TAKEOVER`)."""
        try:
            self.conn.execute("INSERT INTO exec_locks(key, created_at) VALUES (?, ?)", (key, now))
        except sqlite3.IntegrityError:
            self.conn.rollback()
            ok = self._takeover(key, now)
            self.conn.commit()
            return ok
        self.conn.commit()
        return True

    def inflight(
        self,
        *,
        account_id: str,
        market: str,
        marks: dict,
        now: str | None = None,
        exclude_key: str | None = None,
    ) -> list[dict]:
        """판정 포트폴리오에 들어가지 않은 살아 있는 매수 예약 [{key, ticker, amount}].

        `marks`(`ledger.ledger_marks`)는 포트폴리오가 원장을 읽은 순간의 최대 주문·승인 id·시각이다.
        이미 센 것 = 묶인 주문 id ≤ 표시, 또는 묶인 승인 id ≤ 표시이고 그때 pending 이었다. 죽은
        주문·닫힌/만료 승인·보내지도 묶이지도 않은 채 오래된 키는 뺀다. 브로커로 보낸 키(sent_at)는
        승인이 닫혀도 남긴다 — 접수 뒤 주문 행 기록이 실패한 주문은 KIS 에 살아 있을 수 있다."""
        now = now or now_iso()
        rows = self.conn.execute(
            "SELECT k.key, r.ticker, r.amount FROM exec_locks k "
            "JOIN buy_reservations r ON r.key = k.key "
            "LEFT JOIN orders o ON o.id = k.order_id "
            "LEFT JOIN approvals a ON a.id = k.approval_id "
            "WHERE r.account_id=? AND r.market=? AND r.side='BUY' AND k.key != ? "
            f"AND (o.id IS NULL OR o.status NOT IN ({dead_status_sql()})) "
            "AND (a.id IS NULL OR k.sent_at IS NOT NULL OR (a.status NOT IN "
            + _CLOSED_APPROVALS
            + " AND NOT (a.status='pending' AND a.expires_at <= ?))) "
            "AND NOT (k.order_id IS NULL AND k.approval_id IS NULL AND k.sent_at IS NULL "
            "AND k.created_at < ?) "
            "AND NOT ("
            "(k.order_id IS NOT NULL AND k.order_id <= ?) "
            "OR (k.approval_id IS NOT NULL AND k.approval_id <= ? "
            "AND (a.decided_at IS NULL OR a.decided_at > ?)))",
            (
                account_id,
                market,
                exclude_key or "",
                now,
                self._stale_before(now),
                int(marks.get("order_id") or 0),
                int(marks.get("approval_id") or 0),
                str(marks.get("at") or ""),
            ),
        ).fetchall()
        return [{"key": r["key"], "ticker": r["ticker"], "amount": r["amount"]} for r in rows]

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
        """매수 예약으로 키를 잡는다. 한 쓰기 트랜잭션(BEGIN IMMEDIATE) 안에서 진행 중 예약을 읽어
        `recheck(inflight) -> (ok, amount)` 에 넘기고, ok 면 키와 예약(amount)을 함께 쓴다.
        같은 계좌의 다른 경로가 서로의 예약을 본 뒤에만 키를 잡으므로 같은 여유를 둘이 쓰는 경합이
        닫힌다. (잡았나, 본 진행 중 예약)."""
        conn = self.conn
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            inflight = self.inflight(
                account_id=account_id, market=market, marks=marks, now=now, exclude_key=key
            )
            ok, amount = recheck(inflight)
            if not ok:
                conn.rollback()
                return False, inflight
            try:
                conn.execute("INSERT INTO exec_locks(key, created_at) VALUES (?, ?)", (key, now))
            except sqlite3.IntegrityError:
                if not self._takeover(key, now):
                    conn.rollback()
                    return False, inflight
            if amount is not None:
                conn.execute(
                    "INSERT INTO buy_reservations(key, account_id, market, ticker, side, amount) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (key, account_id, market, ticker, side, str(amount)),
                )
            conn.commit()
            return True, inflight
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise

    def bind(
        self,
        key: str,
        *,
        order_id: int | None = None,
        approval_id: int | None = None,
        at: str | None = None,
    ) -> None:
        """키를 주문 행(접수·unknown) 또는 승인 요청에 묶는다. 예약의 `bound_at` 은 묶인 시각 —
        그 뒤에 잔고를 본 판정은 이 주문·요청을 스냅샷으로 본다(그 전 판정에는 진행 중 예약)."""
        when = at or now_iso()
        if order_id is not None:
            self.conn.execute("UPDATE exec_locks SET order_id=? WHERE key=?", (order_id, key))
        if approval_id is not None:
            self.conn.execute("UPDATE exec_locks SET approval_id=? WHERE key=?", (approval_id, key))
        if order_id is not None or approval_id is not None:
            self.conn.execute("UPDATE buy_reservations SET bound_at=? WHERE key=?", (when, key))
        self.conn.commit()

    def mark_sent(self, key: str, *, sent_at: str) -> None:
        """브로커 호출 직전에 남긴다 — 이후로는 응답이 없어도 주문이 KIS 에 있을 수 있다."""
        self.conn.execute("UPDATE exec_locks SET sent_at=? WHERE key=?", (sent_at, key))
        self.conn.commit()

    def release(self, key: str) -> None:
        """브로커가 거부·실패한 주문·주문 전 예외의 키를 지운다(예약은 CASCADE 로 함께)."""
        self.conn.execute("DELETE FROM exec_locks WHERE key=?", (key,))
        self.conn.commit()

    def has_approval(self, key: str) -> bool:
        row = self.conn.execute("SELECT approval_id FROM exec_locks WHERE key=?", (key,)).fetchone()
        return row is not None and row["approval_id"] is not None

    def list_all(self) -> list[dict]:
        """남아 있는 실행 키 전부(오래된 순). `sent_at` 이 있고 `order_id` 가 없는 키는 보낸 뒤
        기록 전에 끊긴 것 — 사람이 KIS 주문 유무를 확인하고 풀어 준다."""
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT key, created_at, order_id, approval_id, sent_at FROM exec_locks "
                "ORDER BY created_at, key"
            ).fetchall()
        ]
