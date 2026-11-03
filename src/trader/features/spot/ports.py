"""spot 포트 `stock_exec`: 다른 레인(단타)이 `Boot.ports["stock_exec"]` 로 받는 현물 집행 서비스
(커널 `execution.ports.StockExecPort`). 주문은 spot `executor.execute`(게이트·승인·멱등·기록),
취소는 커널 `core.cancel`, 체결 적재는 spot `fills.sync_fills` 를 그대로 지난다 — 여기서는
호출마다 ExecContext 를 조립해 넘기기만 한다."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from trader.features.spot import executor, fills
from trader.features.spot.schemas import Decision
from trader.kernel.execution import core
from trader.kernel.registry.types import Port


class StockExecPortImpl:
    def __init__(self, settings: Any, ports: Mapping[str, Any]) -> None:
        self._settings = settings
        self._ports = ports  # Boot.ports 와 같은 dict — 계좌 포트는 호출 때 지연 참조한다

    def _ctx(
        self,
        conn: sqlite3.Connection,
        notifier: Any,
        now: Callable[[], datetime],
        state: Path | None,
    ) -> executor.ExecContext:
        return executor.exec_ctx_from(
            self._settings,
            conn,
            self._ports.get("accounts"),
            notifier,
            now,
            state,
            ports=self._ports,
        )

    def execute(
        self,
        conn: sqlite3.Connection,
        *,
        decision: dict,
        date: date,
        market: str,
        price: Decimal,
        exchange: str | None,
        dry_run: bool,
        scalp_leg: str,
        skip_accounts: frozenset[str],
        max_qty: dict[str, int] | None,
        trade_block: str | None,
        notifier: Any,
        now: Callable[[], datetime],
        state: Path | None,
    ) -> list[dict]:
        """결정 dict 를 `Decision` 으로 검증하고 spot executor 로 계좌마다 판정·주문한다."""
        dec = Decision.model_validate(decision)
        ectx = self._ctx(conn, notifier, now, state)
        return executor.execute(
            ectx,
            dec,
            date=date,
            market=market,
            price=price,
            exchange=exchange,
            dry_run=dry_run,
            scalp_leg=scalp_leg,
            skip_accounts=skip_accounts,
            max_qty=max_qty,
            trade_block=trade_block,
        )

    def cancel(
        self,
        conn: sqlite3.Connection,
        *,
        account_id: str,
        order_id: int,
        order_no: str,
        market: str,
        exchange: str | None,
        dry_run: bool,
        reason: str,
        notifier: Any,
        now: Callable[[], datetime],
    ) -> dict:
        """미체결 주문 1건을 커널 `core.cancel` 로 취소한다(브로커 취소의 유일한 경로)."""
        ectx = self._ctx(conn, notifier, now, None)
        return core.cancel(
            executor.deps_of(ectx),
            order_id=order_id,
            order_no=order_no,
            market=market,
            broker=ectx.brokers.get(account_id),
            dry_run=dry_run,
            reason=reason,
            exchange=exchange,
        )

    def sync_fills(
        self,
        conn: sqlite3.Connection,
        *,
        account_id: str,
        d: date,
        now: Callable[[], datetime],
        notifier: Any,
    ) -> dict:
        """계좌 1개의 체결을 spot `fills.sync_fills` 로 적재한다. 계좌·브로커가 없으면 예외 없이
        `{"error": …}`."""
        ectx = self._ctx(conn, notifier, now, None)
        account = next((a for a in ectx.accounts if a.id == account_id), None)
        if account is None:
            return {"error": f"계좌 없음: {account_id}"}
        broker = ectx.brokers.get(account_id)
        if broker is None:
            return {"error": f"계좌 {account_id} 브로커 없음"}
        return fills.sync_fills(conn, broker, account, d, now=now, notifier=notifier)


def build_port(boot: Any) -> StockExecPortImpl:
    # 조립 중인 Boot.ports 는 아직 빈 dict 일 수 있다 — `or {}` 로 바꾸면 지연 참조가 끊긴다
    ports = getattr(boot, "ports", None)
    return StockExecPortImpl(boot.settings, ports if ports is not None else {})


PORT = Port("stock_exec", build_port)
