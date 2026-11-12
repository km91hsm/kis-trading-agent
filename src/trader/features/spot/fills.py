"""체결 동기화: 브로커 체결 조회 → fills 멱등 적재 → orders.status·filled_qty 갱신.

주문번호가 원장에 없는 체결(앱·HTS 수동 주문)은 mode='external' 주문 행을 만들고 그 id 로 체결을
붙인다(`fills.order_id` 는 NOT NULL). 그날 체결 조회를 마친 뒤(마감 이후) 체결이 없는
accepted·unknown 주문과 잔량이 남은 partial 주문은 expired 로 바꾼다 — 당일 주문은 마감에
소멸하므로 "조회 성공 + 마감" 이 곧 "주문이 죽었다" 이고, 그래야 미체결 주문이 중복 방지 조건에
영구히 남지 않는다. 장중 수동 실행이나 조회 실패 때는 건드리지 않는다.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from trader.features.spot import labels
from trader.kernel.broker.models import Fill
from trader.kernel.config.market_hours import market_close
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.execution import ledger as xledger
from trader.kernel.execution.types import DEAD_STATUSES
from trader.kernel.ledger import control
from trader.kernel.notify import messages

logger = logging.getLogger("trader.spot")

# 정리되지 않은 지난 주문을 다시 조회하는 날짜 수 상한(실행당, 오래된 순). 범위 제한은 없다 —
# 8일 넘게 꺼져 있던 뒤의 접수·unknown 주문도 다시 조회해 마감 뒤 expired 로 정리한다.
# 상한 밖 날짜는 앞의 주문이 정리된 다음 실행이 이어 본다
STALE_QUERY_CAP = 10
# 응답 없는 주문이 KIS 당일 주문 목록에 없을 때 "접수 안 됨" 으로 확정하기까지 기다리는 시간
# (KIS 반영 지연을 미접수로 오판하지 않게)
UNKNOWN_GRACE = timedelta(minutes=2)
SUSPECT_PREFIX = "미접수 의심:"  # 유예 뒤 첫 부재 표시(orders.message) — 다음 조회에도 없으면 확정


def _day_for(market: str, d: date | datetime) -> date:
    """datetime 이면 그 시장 현지 날짜(KIS 체결 조회의 주문일자는 현지 기준), date 면 그대로."""
    if isinstance(d, datetime):
        return d.astimezone(MARKET_TZ[market]).date()
    return d


def _order_id_for(
    conn: sqlite3.Connection, account_id: str, f: Fill, *, notifier: Any | None = None
) -> int:
    # KIS 주문번호(ODNO)는 날마다 다시 시작하므로 종목·방향이 같고 **같은 현지 날짜**에 낸 주문만
    # 같은 주문으로 본다 (옛 주문에 붙이면 그 주문의 기존 체결을 지워 버린다)
    local_day = _day_for(f.market, f.filled_at)
    lo, hi = xledger.day_bounds(local_day, f.market)
    row = conn.execute(
        "SELECT id FROM orders WHERE account_id=? AND order_no=? AND ticker=? AND side=? "
        "AND created_at >= ? AND created_at < ? ORDER BY id DESC LIMIT 1",
        (account_id, f.order_no, f.ticker, f.side, lo, hi),
    ).fetchone()
    if row:
        return int(row["id"])
    # 응답을 못 받아 unknown(주문번호 없음)으로 남은 주문은 같은 날·종목·방향 체결로 확정한다 —
    # 단 **후보가 정확히 1건**일 때만. 둘 이상이면 어느 주문의 체결인지 알 수 없어 붙이지 않고
    # 알림 1회 + control 플래그로 사용자 확인에 맡긴다.
    rows = conn.execute(
        "SELECT id FROM orders WHERE account_id=? AND ticker=? AND side=? AND status='unknown' "
        "AND order_no IS NULL AND created_at >= ? AND created_at < ? ORDER BY id",
        (account_id, f.ticker, f.side, lo, hi),
    ).fetchall()
    if len(rows) > 1:
        _flag_ambiguous(conn, account_id, f, local_day, [int(r["id"]) for r in rows], notifier)
    if len(rows) == 1:
        conn.execute(
            "UPDATE orders SET order_no=?, status='accepted' WHERE id=?",
            (f.order_no, rows[0]["id"]),
        )
        conn.commit()
        logger.info(
            "미확인 주문 확정 %s %s %s → 접수번호 %s", account_id, f.ticker, f.side, f.order_no
        )
        return int(rows[0]["id"])
    return xledger.insert_order(
        conn,
        decision_id=None,
        account_id=account_id,
        ticker=f.ticker,
        market=f.market,
        side=f.side,
        qty=f.qty,
        order_type="LIMIT",
        price=f.price,
        status="accepted",
        order_no=f.order_no,
        message="원장 밖에서 낸 주문(체결 동기화로 생성)",
        mode="external",
        created_at=f.filled_at.isoformat(),
        asset="stock",
        origin="external",
    )


def resolve_unknown(
    conn: sqlite3.Connection,
    broker: Any,
    account_id: str,
    market: str,
    day: date,
    *,
    now: datetime,
    notifier: Any | None = None,
) -> dict:
    """응답을 못 받아 unknown(주문번호 없음)으로 남은 day 의 주문을 KIS 당일 주문 목록으로 확정한다.

    체결 조회만으로는 "접수됐지만 미체결" 과 "접수 안 됨" 을 가를 수 없어 unknown 손절 SELL 이
    마감까지 다음 손절을 막는다. 같은 종목·방향의 **낯선 주문**(원장에 그 주문번호가 없는 주문)을
    후보로 본다:
    - 후보 1건·unknown 1건·수량 같음 → 주문번호를 붙여 accepted (이후 체결은 주문번호로 붙는다)
    - 후보 없음·주문 뒤 `UNKNOWN_GRACE` 지남·**손절 매도(origin=stop)** → 첫 번째는 의심으로만
      적고, 다음 조회에도 없으면 접수 안 됨: failed (죽은 주문 — 실행 키는 다음 선점이
      넘겨받는다) + 알림. 매수·판단 매도는 확정하지 않는다(중복 주문을 KIS 가 막아 주지 않는다)
    - 그 밖(후보 여럿·수량 다름·유예 안) → 그대로 둔다
    주문 목록 조회를 지원하지 않는 브로커·조회 실패는 아무것도 바꾸지 않는다."""
    out = {"accepted": 0, "failed": 0}
    lo, hi = xledger.day_bounds(day, market)
    unknown = conn.execute(
        "SELECT id, ticker, side, qty, created_at, message, COALESCE(origin, 'llm') AS origin "
        "FROM orders WHERE account_id=? AND market=? "
        "AND status='unknown' AND order_no IS NULL AND created_at >= ? AND created_at < ? "
        "ORDER BY id",
        (account_id, market, lo, hi),
    ).fetchall()
    get_orders = getattr(broker, "get_orders", None)
    if not unknown or get_orders is None:
        return out
    try:
        seen = get_orders(market, day)
    except Exception as e:  # noqa: BLE001 - 확정하지 못하면 unknown 그대로(보수적)
        logger.warning("당일 주문 목록 조회 실패 %s %s: %s", account_id, market, e)
        return out
    known: set[str] = set()
    external: set[str] = set()  # 체결 동기화가 만든 행 — unknown 주문의 체결일 수도 있다(모호)
    for r in conn.execute(
        "SELECT order_no, mode FROM orders WHERE account_id=? AND market=? "
        "AND order_no IS NOT NULL AND created_at >= ? AND created_at < ?",
        (account_id, market, lo, hi),
    ):
        known.add(r["order_no"])
        if r["mode"] == "external":
            external.add(r["order_no"])
    groups: dict[tuple[str, str], list[Any]] = {}
    for r in unknown:
        groups.setdefault((r["ticker"], r["side"]), []).append(r)
    for (ticker, side), rows in groups.items():
        cands = [
            o for o in seen if o.ticker == ticker and o.side == side and o.order_no not in known
        ]
        if len(cands) == 1 and len(rows) == 1 and cands[0].qty == int(rows[0]["qty"]):
            conn.execute(
                "UPDATE orders SET order_no=?, status='accepted' WHERE id=?",
                (cands[0].order_no, rows[0]["id"]),
            )
            conn.commit()
            out["accepted"] += 1
            logger.info(
                "미확인 주문 접수 확정 %s %s %s → 접수번호 %s",
                account_id, ticker, side, cands[0].order_no,
            )  # fmt: skip
            continue
        if cands or any(
            o.ticker == ticker and o.side == side and o.order_no in external for o in seen
        ):
            continue  # 어느 주문인지 가를 수 없다 — 체결이 오면 기존 모호 처리(`_flag_ambiguous`)
        for r in rows:
            # 미접수 확정은 손절 매도만: failed 가 되면 실행 키가 넘어가 같은 주문이 다시 나갈 수
            # 있다. 전량 손절은 중복이 나가도 KIS 주문가능수량이 막지만 매수·부분 매도는 막을 것이
            # 없다 — 그런 주문은 예전처럼 마감 만료까지 살아 있는 주문으로 둔다
            if r["side"] != "SELL" or r["origin"] != "stop":
                continue
            if now - datetime.fromisoformat(r["created_at"]) < UNKNOWN_GRACE:
                continue
            if not str(r["message"] or "").startswith(SUSPECT_PREFIX):
                # 첫 부재는 의심으로만 적는다 — 목록 한 번의 누락·반영 지연으로 죽이지 않게
                conn.execute(
                    "UPDATE orders SET message=? WHERE id=? AND status='unknown'",
                    (f"{SUSPECT_PREFIX} {now.isoformat()}", r["id"]),
                )
                conn.commit()
                continue
            cur = conn.execute(
                "UPDATE orders SET status='failed', message=? WHERE id=? AND status='unknown'",
                ("미접수 확정: KIS 당일 주문 목록에 두 번 연속 없음", r["id"]),
            )
            conn.commit()
            if cur.rowcount != 1:
                continue
            out["failed"] += 1
            logger.warning(
                "미확인 주문 미접수 확정 %s %s %s #%s", account_id, ticker, side, r["id"]
            )
            if notifier is not None:
                try:
                    notifier.send(
                        messages.order_not_accepted(
                            account=account_id,
                            market=market,
                            label=labels.label(ticker, labels.resolve(conn, market, ticker)),
                            side=side,
                            qty=r["qty"],
                        )
                    )
                except Exception as e:  # noqa: BLE001 - 알림 실패가 동기화를 막지 않는다
                    logger.warning("미접수 확정 알림 실패: %s", e)
    return out


def _refresh_status(conn: sqlite3.Connection, order_id: int) -> bool:
    """누적 체결로 orders.filled_qty·status 를 맞춘다. 상태가 바뀌었으면 True."""
    o = conn.execute(
        "SELECT qty, status, filled_qty FROM orders WHERE id=?", (order_id,)
    ).fetchone()
    filled = int(
        conn.execute(
            "SELECT COALESCE(SUM(qty), 0) AS q FROM fills WHERE order_id=?", (order_id,)
        ).fetchone()["q"]
    )
    if filled <= 0:
        return False
    status = "filled" if filled >= int(o["qty"]) else "partial"
    if status == "partial" and o["status"] in DEAD_STATUSES:
        return False  # 마감에 잔량이 만료된 부분체결 주문을 다음 동기화가 다시 살리지 않는다
    if status == o["status"] and filled == int(o["filled_qty"]):
        return False
    xledger.update_order_fill(conn, order_id, filled_qty=filled, status=status)  # type: ignore[arg-type]
    return status != o["status"]


def stale_open_days(
    conn: sqlite3.Connection,
    account_id: str,
    market: str,
    today: date,
    *,
    cap: int = STALE_QUERY_CAP,
) -> list[date]:
    """오늘(현지) 이전에 아직 살아 있는(접수·부분체결·응답없음) 주문이 있는 현지 날짜들(오래된 순,
    실행당 `cap` 개). 그날 체결 동기화가 실패하거나 놓치면 다음 날부터는 오늘만 조회해 영구히
    '접수' 로 남던 주문을 다시 조회한다."""
    hi, _ = xledger.day_bounds(today, market)
    rows = conn.execute(
        "SELECT DISTINCT created_at FROM orders WHERE account_id=? AND market=? "
        "AND status IN ('accepted','partial','unknown') AND created_at < ?",
        (account_id, market, hi),
    ).fetchall()
    days = sorted({_day_for(market, datetime.fromisoformat(r[0])) for r in rows})
    if len(days) > cap:
        logger.warning(
            "미정리 주문 날짜 %d개 중 %d개만 이번에 조회 %s %s (나머지는 다음 실행)",
            len(days),
            cap,
            account_id,
            market,
        )
    return days[:cap]


def _session_closed(market: str, day: date, now: datetime) -> bool:
    """day 의 정규장이 끝났는가 (시장 현지 기준)."""
    local = now.astimezone(MARKET_TZ[market])
    if local.date() > day:
        return True
    return local.date() == day and local.time() >= market_close(market, day)


def expire_unfilled(conn: sqlite3.Connection, account_id: str, market: str, day: date) -> int:
    """day(현지)의 체결 없는 accepted·unknown 주문과 잔량이 남은 partial 주문을 expired 로
    (마감 뒤인지는 호출자가 본다). 당일 주문은 마감에 소멸하므로 partial 의 잔량도 죽었다 —
    살아 있는 상태로 두면 중복 방지 조건에 영구히 남는다(체결분은 fills 에 그대로)."""
    lo, hi = xledger.day_bounds(day, market)
    cur = conn.execute(
        "UPDATE orders SET status='expired' WHERE account_id=? AND market=? "
        "AND created_at >= ? AND created_at < ? AND ("
        "(status IN ('accepted','unknown') AND id NOT IN (SELECT order_id FROM fills)) "
        "OR status='partial')",
        (account_id, market, lo, hi),
    )
    conn.commit()
    if cur.rowcount:
        logger.info("미체결 주문 %d건 만료 처리 %s %s %s", cur.rowcount, account_id, market, day)
    return cur.rowcount


def _flag_ambiguous(
    conn: sqlite3.Connection,
    account_id: str,
    f: Fill,
    local_day: date,
    order_ids: list[int],
    notifier: Any | None,
) -> None:
    key = f"fill_ambiguous:{f.market}:{local_day.isoformat()}"
    cur = control.get_flag(conn, key) or {}
    cases = cur.get("cases") or []
    case = {"account_id": account_id, "ticker": f.ticker, "side": f.side, "order_ids": order_ids}
    if case in cases:
        return
    cases.append(case)
    control.set_flag(conn, key, {"cases": cases, "candidates": len(order_ids)})
    logger.warning(
        "체결 매칭 모호 %s %s %s: unknown 후보 %s", account_id, f.ticker, f.side, order_ids
    )
    if notifier is not None:
        try:
            notifier.send(
                messages.fill_ambiguous(
                    account=account_id,
                    market=f.market,
                    label=labels.label(f.ticker, labels.resolve(conn, f.market, f.ticker)),
                    side=f.side,
                    detail=(
                        f"응답 없는 주문 {len(order_ids)}건"
                        f"(#{', #'.join(str(i) for i in order_ids)}) 중 어느 것의 체결인지 알 수 "
                        "없어 붙이지 않았어요 — KIS 앱에서 확인 뒤 정리"
                    ),
                )
            )
        except Exception:  # noqa: BLE001
            logger.debug("체결 매칭 모호 알림 실패", exc_info=True)


def sync_fills(
    conn: sqlite3.Connection,
    broker: Any,
    account: Any,
    d: date | datetime,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    notifier: Any | None = None,
) -> dict:
    """계좌의 시장별 체결을 적재한다. 같은 체결은 두 번 들어가지 않는다 (fills 유니크 인덱스).

    d 가 datetime 이면 시장마다 현지 날짜로 조회한다 (US 는 ET: 07:10 KST 동기화는 전날 ET).
    조회가 성공했고 그날 정규장이 끝났으면 체결 없는 accepted·unknown 주문을 expired 로 바꾼다.
    """
    inserted = 0
    replaced = 0
    expired = 0
    updated: set[int] = set()
    failed: list[str] = []  # 조회 실패 시장(호출자가 '체결 확인됨' 으로 보지 않게)
    days = {m: _day_for(m, d) for m in account.markets}
    for market in account.markets:
        resolve_unknown(
            conn, broker, account.id, market, days[market], now=now(), notifier=notifier
        )
        try:
            fills = broker.get_fills(market, days[market])
        except Exception as e:  # noqa: BLE001 - 한 시장 실패가 다른 시장을 막지 않는다
            logger.warning("체결 조회 실패 %s %s: %s", account.id, market, e)
            failed.append(market)
            continue
        for f in fills:
            oid = _order_id_for(conn, account.id, f, notifier=notifier)
            # KIS 체결 조회는 주문당 누적 체결수량을 주므로 기존 행이 있으면 누적값으로 바꾼다
            existing = conn.execute(
                "SELECT COALESCE(SUM(qty), 0) AS q FROM fills WHERE order_id=?", (oid,)
            ).fetchone()["q"]
            if int(existing) == f.qty:
                _refresh_status(conn, oid)
                continue
            try:  # 교체(DELETE + INSERT)는 한 트랜잭션 — 중간에 끊기면 체결이 사라진다
                if int(existing) > 0:
                    conn.execute("DELETE FROM fills WHERE order_id=?", (oid,))
                    replaced += 1
                fid = xledger.insert_fill(
                    conn,
                    order_id=oid,
                    account_id=account.id,
                    market=f.market,
                    ticker=f.ticker,
                    side=f.side,
                    qty=f.qty,
                    price=Decimal(f.price),
                    filled_at=f.filled_at.isoformat(),
                    order_no=f.order_no,
                )
            except sqlite3.Error:
                conn.rollback()
                raise
            if fid is not None:
                inserted += 1
            if _refresh_status(conn, oid):
                updated.add(oid)
        if _session_closed(market, days[market], now()):
            expired += expire_unfilled(conn, account.id, market, days[market])
    return {
        "account_id": account.id,
        "date": (
            {m: day.isoformat() for m, day in days.items()}
            if isinstance(d, datetime)
            else d.isoformat()
        ),
        "inserted": inserted - replaced,
        "updated": replaced,
        "orders_updated": len(updated),
        "expired": expired,
        "failed_markets": failed,
    }
