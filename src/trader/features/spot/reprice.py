"""아침 미체결 재호가 (Phase 23 B).

개장 뒤 `risk.reprice.after_min` 분에 운영일 1회(`reprice_done:<market>:<date>` 플래그): 오늘 낸
지정가 주문 중 체결이 0 인 `accepted` 를 **`core.cancel` 로 취소 → 같은 결정으로 현재가 지정가
재제출**한다. 재제출은 `executor.execute(is_reprice=True)` 라 게이트(비중·현금·중복·국면)를 다시
통과하고 체크포인트는 `42_reprice_<acct>`·`52_order_reprice_<acct>` 다. 취소된 원 주문은 dead 라
중복 가드에 걸리지 않는다. 원 지정가 대비 `max_chase_pct` 넘게 불리한(BUY 는 더 높은, SELL 은 더
낮은) 현재가면 재제출하지 않고 알림만. dry_run·`off` 계좌·unknown(번호 없음)·외부 주문은
건너뛴다. `quote_fn` 이 시세 객체(`.price`·`.change_pct`·종목상태)를 주면 재제출 게이트가
당일 등락률(급등 추격 금지)·매매 제한을 본다(2-O5) — Decimal 만 주면 가격만."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from trader.features.spot import executor, tradability
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.schemas import Decision
from trader.features.spot.settings import RepriceCfg
from trader.kernel.broker.errors import KisApiError
from trader.kernel.execution import core
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import control
from trader.kernel.notify import messages

logger = logging.getLogger("trader.spot")

_BUY_ACTIONS = ("BUY", "ADD")
_SELL_ACTIONS = ("SELL", "TRIM")


def done_key(market: str, today: date) -> str:
    return f"reprice_done:{market}:{today.isoformat()}"


def candidates(conn: sqlite3.Connection, account_id: str, market: str, today: date) -> list[dict]:
    """오늘 낸 지정가 accepted 주문 중 체결 0·결정 있음·외부 아님 + **그 결정이 낸 주문**만.

    손절 SELL(결정은 며칠 전 BUY)·헤지(결정 source='hedge')는 재호가가 "최신 결정 + 일반 게이트"
    로 재제출하면 손절이 사라지거나(BUY 로 재실행) bear 에서 헤지가 거부되므로 뺀다 — 주문 방향이
    결정 행동(BUY/ADD→BUY, SELL/TRIM→SELL)과 같고 일반(LLM) 결정일 때만(전체 검토 O2). 주문 행의
    origin 이 stop·hedge 인 것은 원장 조회(`open_limit_orders_today`)가 이미 뺀다 — 최신 결정이
    SELL/TRIM 인 날의 US 손절 지정가 SELL 은 방향이 같아도 손절이라 빠진다(2-O3). 이미 재호가로
    다시 낸 주문(origin reprice)도 뺀다 — 틱 중간 예외로 완료 플래그가 안 서도 다음 틱이 재제출분을
    다시 쫓지 않는다."""
    out = []
    for r in xledger.open_limit_orders_today(
        conn, account_id, market, today, exclude_origins=("stop", "hedge", "reprice")
    ):
        if r["status"] != "accepted" or r["order_type"] != "LIMIT" or not r["order_no"]:
            continue
        if r["mode"] == "external" or r["price"] is None or r["decision_id"] is None:
            continue
        d = spot_ledger.get_decision(conn, int(r["decision_id"]))
        if d is None or d["source"] == "hedge":
            continue
        want = _BUY_ACTIONS if r["side"] == "BUY" else _SELL_ACTIONS
        if d["action"] not in want:
            continue
        filled = conn.execute(
            "SELECT COALESCE(SUM(qty), 0) AS q FROM fills WHERE order_id=?", (r["id"],)
        ).fetchone()["q"]
        if int(filled or 0) == 0:
            out.append(r)
    return out


def too_far(side: str, limit: Decimal, quote: Decimal, max_chase_pct: float) -> bool:
    """재제출을 포기할 만큼 불리한가: BUY 는 현재가 > 원 지정가 × (1+max), SELL 은 < (1−max)."""
    m = Decimal(str(max_chase_pct))
    if side == "BUY":
        return quote > limit * (1 + m)
    return quote < limit * (1 - m)


def run_once(
    ctx: executor.ExecContext,
    *,
    market: str,
    today: date,
    cfg: RepriceCfg,
    quote_fn: Callable[[str, str | None], Any],
    exchanges: dict[str, str] | None = None,
    dry_run: bool = False,
    label: Callable[[str], str] | None = None,
) -> dict:
    """운영일 1회. 반환 {repriced:[...], skipped:[...]}. 시각 조건은 호출자(데몬)가 본다."""
    if not cfg.enabled:
        return {"skipped": "reprice.enabled=false"}
    if dry_run:
        return {"skipped": "dry-run"}
    if control.get_flag(ctx.conn, done_key(market, today)):
        return {"skipped": "오늘 이미 실행"}
    if ctx.paused(ctx.conn):
        return {"skipped": "일시정지"}
    name = label or (lambda t: t)
    deps = executor.deps_of(ctx)
    out: dict[str, Any] = {"repriced": [], "skipped": []}
    for account in ctx.accounts:
        if market not in account.markets:
            continue
        mode, _ = ctx.policy(ctx.conn, account)
        broker = ctx.brokers.get(account.id)
        if mode == "off" or broker is None:
            continue
        for row in candidates(ctx.conn, account.id, market, today):
            ticker = row["ticker"]
            exchange = (exchanges or {}).get(ticker)
            limit = Decimal(str(row["price"]))
            try:
                q = quote_fn(ticker, exchange)
                quote = Decimal(str(getattr(q, "price", q)))
            except (KisApiError, ValueError, ArithmeticError) as e:
                out["skipped"].append({"order_id": row["id"], "why": f"시세 실패: {e}"})
                continue
            if quote <= 0:
                out["skipped"].append({"order_id": row["id"], "why": "시세 0"})
                continue
            if too_far(row["side"], limit, quote, cfg.max_chase_pct):
                why = f"현재가 {quote} 가 원 지정가 {limit} 대비 {cfg.max_chase_pct:.1%} 넘게 불리"
                out["skipped"].append({"order_id": row["id"], "why": why})
                core.notify_quietly(
                    ctx.notifier,
                    messages.reprice_deferred(
                        account=account.id,
                        market=market,
                        label=name(ticker),
                        side=row["side"],
                        qty=row["qty"],
                        limit=limit,
                        why=why,
                    ),
                )
                continue
            try:
                res = core.cancel(
                    deps,
                    order_id=int(row["id"]),
                    order_no=str(row["order_no"]),
                    market=market,
                    broker=broker,
                    dry_run=False,
                    reason="아침 재호가",
                    exchange=exchange,
                )
            except (KisApiError, ValueError) as e:
                out["skipped"].append({"order_id": row["id"], "why": f"취소 실패: {e}"})
                logger.warning(
                    "재호가 취소 실패 %s %s #%s: %s", account.id, ticker, row["order_no"], e
                )
                continue
            if not res.get("ok"):
                why = f"취소 거부: {res.get('message')}"
                out["skipped"].append({"order_id": row["id"], "why": why})
                continue
            dec_row = spot_ledger.get_decision(ctx.conn, int(row["decision_id"]), hydrate=True)
            if dec_row is None:
                out["skipped"].append({"order_id": row["id"], "why": "결정 행 없음(취소만)"})
                continue
            dec = Decision(**dec_row["data"]).model_copy(
                update={"order_type": "LIMIT", "limit_price": float(quote)}
            )
            results = executor.execute(
                ctx,
                dec,
                date=today,
                market=market,
                price=quote,
                exchange=exchange,
                dry_run=False,
                decision_id=int(row["decision_id"]),
                skip_accounts=frozenset(a.id for a in ctx.accounts if a.id != account.id),
                extra_note=f"재호가 {limit}→{quote} (원 주문 #{row['id']} 취소)",
                is_reprice=True,
                change_pct=getattr(q, "change_pct", None),
                trade_block=tradability.block_reason(q) if row["side"] == "BUY" else None,
            )
            r0 = results[0] if results else {}
            out["repriced"].append(
                {
                    "order_id": row["id"],
                    "account_id": account.id,
                    "ticker": ticker,
                    "side": row["side"],
                    "from": str(limit),
                    "to": str(quote),
                    "approved": bool(r0.get("approved")),
                    "new_order": (r0.get("order") or {}).get("status"),
                }
            )
            tail = (
                "재제출"
                if r0.get("approved")
                else f"게이트 거부: {'; '.join(r0.get('reasons') or [])}"
            )
            core.notify_quietly(
                ctx.notifier,
                messages.reprice(
                    account=account.id,
                    market=market,
                    label=name(ticker),
                    side=row["side"],
                    qty=row["qty"],
                    old=limit,
                    new=quote,
                    tail=tail,
                ),
            )
    control.set_flag(ctx.conn, done_key(market, today), True)
    return out
