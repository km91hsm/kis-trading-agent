"""야간 KIS 대조. 브로커 잔고가 진실이고 원장은 그 사본이다.

계좌·시장별로 ① 브로커 보유 수량 vs 원장 최신 `positions_snapshot` 수량, ② 마감 뒤에도 살아
있는 원장 주문(accepted·unknown·partial — 체결 동기화가 expired 로 바꿨어야 함)을 대조한다.
불일치가 있으면 알림 + `reconcile:<account>:<market>:<date>` 플래그 + `positions_snapshot` 을
브로커 값으로 덧쓴다(주문·체결 행은 건드리지 않는다 — 추적할 사실이 사라지면 안 된다). 같은
계좌·시장·날짜의 알림은 하루 한 번만 나간다. 네트워크 실패는 그 계좌의 `error` 로 남기고 다음
계좌로 간다."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from trader.features.spot import ledger as spot_ledger
from trader.kernel.execution import ledger as xledger
from trader.kernel.execution.types import dead_status_sql
from trader.kernel.ledger import control
from trader.kernel.notify import messages

logger = logging.getLogger("trader.spot")


def flag_key(account_id: str, market: str, today: date) -> str:
    return f"reconcile:{account_id}:{market}:{today.isoformat()}"


def compare_positions(ledger: list[dict], broker: list[Any]) -> list[dict]:
    """원장 스냅샷 행(`{ticker, qty}`) vs 브로커 Position. 수량이 다르거나 한쪽에만 있는 종목."""
    l_qty = {str(p["ticker"]): int(p.get("qty") or 0) for p in ledger}
    b_qty = {p.ticker: int(p.qty) for p in broker if int(p.qty) > 0}
    out: list[dict] = []
    for t in sorted(set(l_qty) | set(b_qty)):
        lq, bq = l_qty.get(t, 0), b_qty.get(t, 0)
        if lq != bq:
            out.append({"ticker": t, "ledger_qty": lq, "broker_qty": bq})
    return out


def open_orders(conn: sqlite3.Connection, account_id: str, market: str) -> list[dict]:
    """마감 뒤에도 살아 있는 주문(체결 완료·죽은 상태 제외). 정상이면 비어 있어야 한다."""
    rows = conn.execute(
        "SELECT id, order_no, ticker, side, qty, status, created_at FROM orders "
        "WHERE account_id=? AND market=? "
        f"AND status NOT IN ({dead_status_sql()},'filled') ORDER BY id",
        (account_id, market),
    ).fetchall()
    return [dict(r) for r in rows]


def reconcile_account(
    conn: sqlite3.Connection,
    broker: Any,
    account: Any,
    market: str,
    *,
    today: date,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    notifier: Any | None = None,
    label: Callable[[str], str] | None = None,
) -> dict:
    """한 계좌·시장 대조. 반환 {account_id, market, mismatches, open_orders, ok, snapshot_fixed}."""
    get_balance = getattr(broker, "get_balance", None)
    if get_balance is not None:
        positions, cash = get_balance(market)
    else:
        positions, cash = broker.get_positions(market), broker.get_cash(market)
    latest = spot_ledger.latest_positions(conn, account.id, market) or {}
    mismatches = compare_positions(latest.get("positions") or [], positions)
    stale = open_orders(conn, account.id, market)
    out: dict[str, Any] = {
        "account_id": account.id,
        "market": market,
        "date": today.isoformat(),
        "ledger_date": latest.get("date"),
        "mismatches": mismatches,
        "open_orders": stale,
        "ok": not mismatches and not stale,
        "snapshot_fixed": False,
    }
    if mismatches:
        nav = spot_ledger.nav_of(positions, cash)
        xledger.snapshot_positions(
            conn,
            account_id=account.id,
            d=today,
            market=market,
            nav=nav,
            cash=cash.total,
            cash_available=cash.available,
            positions=[
                {
                    "ticker": p.ticker,
                    "market": p.market,
                    "qty": p.qty,
                    "avg_price": p.avg_price,
                    "current_price": p.current_price,
                    "eval_amount": p.eval_amount,
                }
                for p in positions
            ],
        )
        out["snapshot_fixed"] = True
    key = flag_key(account.id, market, today)
    prev = control.get_flag(conn, key)
    # 같은 날 불일치 알림은 한 번만 — ok 실행이 뒤의 불일치 알림을 막지 않고, 전송 실패는 재시도
    notified = bool(prev and prev.get("notified"))
    if not out["ok"] and notifier is not None and not notified:
        name = label or (lambda t: t)
        parts = [
            f"{name(m['ticker'])} 원장 {m['ledger_qty']}↔KIS {m['broker_qty']}" for m in mismatches
        ]
        parts += [
            f"주문 #{o['id']} {o['side']} {name(o['ticker'])} {o['qty']}주 {o['status']}"
            "(마감 뒤 미정리)"
            for o in stale
        ]
        try:
            notifier.send(
                messages.reconcile_mismatch(
                    account=account.id, market=market, day=str(today), parts=parts
                )
            )
            notified = True
        except Exception:  # noqa: BLE001 - 알림 실패가 대조를 바꾸지 않는다
            logger.debug("대조 알림 실패", exc_info=True)
    control.set_flag(conn, key, {**out, "at": now().isoformat(), "notified": notified})
    logger.info(
        "대조 %s %s: 불일치 %d, 미정리 주문 %d", account.id, market, len(mismatches), len(stale)
    )
    return out


def reconcile_all(
    conn: sqlite3.Connection,
    brokers: dict[str, Any],
    accounts: list,
    *,
    market: str | None,
    today_for: Callable[[str], date],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    notifier: Any | None = None,
    label: Callable[[str, str], str] | None = None,
) -> dict:
    """모든 현물 계좌(·그 시장)에 대해 대조. 한 계좌의 실패는 `error` 로 남기고 계속한다."""
    out: dict[str, Any] = {}
    for a in accounts:
        b = brokers.get(a.id)
        if b is None or getattr(a, "asset", "stock") != "stock":
            continue
        for m in a.markets:
            if market is not None and m != market:
                continue
            try:
                out[f"{a.id}:{m}"] = reconcile_account(
                    conn,
                    b,
                    a,
                    m,
                    today=today_for(m),
                    now=now,
                    notifier=notifier,
                    label=(lambda t, _m=m: label(_m, t)) if label else None,
                )
            except Exception as e:  # noqa: BLE001 - 한 계좌의 실패가 나머지를 막지 않는다
                logger.warning("대조 실패 %s %s: %s", a.id, m, e)
                out[f"{a.id}:{m}"] = {"error": f"{type(e).__name__}: {e}"}
    return out
