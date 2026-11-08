"""단타 청산: 계좌별 다리·체결 동기화·취소·흡수·청산 단계·이월(옛 엔진 §F 이식).

포지션은 계좌별 다리(`legs`: 요청 수량·진입/청산 주문 id·`dry`·`assumed`·`recheck`)로 든다.
주문·취소·체결 동기화는 전부 포트 `stock_exec`(`execute`·`cancel`·`sync_fills`)로만 낸다 — 레인은
브로커 주문 API 를 직접 부르지 않는다. `engine` 이 이 모듈을 모듈 수준에서 import 하므로 이
모듈은 `engine` 을 함수 안에서만 가져온다(순환 import 방지). `CARRY_DAYS` 는 `engine.CARRY_DAYS`
하나를 정본으로 쓴다."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from trader.features.scalp import signals
from trader.features.scalp.state import (
    advice_key,
    event,
    load_state,
    queue_note,
    save_state,
    state_dir,
)
from trader.kernel.broker.errors import KisApiError
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.execution import ledger as xledger
from trader.kernel.execution.core import notify_quietly
from trader.kernel.execution.types import DEAD_STATUSES
from trader.kernel.ledger import control
from trader.kernel.notify import messages

logger = logging.getLogger("trader.scalp")

EXIT_REQUOTE_S = 120  # 청산 지정가(US)가 이만큼 미체결이면 취소하고 다음 틱에 현재가로 다시 낸다


# --- 계좌별 체결 상태 --------------------------------------------------------------------


def legs(pos: dict) -> dict[str, dict]:
    """포지션의 계좌별 다리. 옛 상태 파일(legs 없음)은 요청 수량을 체결로 보고(`assumed`) 청산
    주문만 추적한다."""
    if "legs" not in pos:
        assumed = "dry" if pos.get("dry_run") else "assumed"
        pos["legs"] = {
            a: {"req": int(q), assumed: True, "buy": [], "sell": []}
            for a, q in (pos.get("qty") or {}).items()
        }
    return pos["legs"]


def live(status: str) -> bool:
    return status not in DEAD_STATUSES and status != "filled"


def orders(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    """주문 행 + 체결 합계(수량·금액)."""
    out = []
    for oid in ids:
        o = conn.execute(
            "SELECT id, order_no, org_no, qty, status, order_type, created_at FROM orders "
            "WHERE id=?",
            (oid,),
        ).fetchone()
        if o is None:
            continue
        q, amt = 0, Decimal(0)
        for f in conn.execute("SELECT qty, price FROM fills WHERE order_id=?", (oid,)):
            q += int(f["qty"])
            amt += Decimal(str(f["price"])) * int(f["qty"])
        out.append(dict(o) | {"filled": q, "filled_amt": amt})
    return out


def leg_view(ctx: Any, leg: dict) -> dict:
    """다리 하나의 체결 현황: 매수·매도 체결 수량/금액, 살아 있는 매수·매도 주문."""
    if leg.get("dry"):
        q = int(leg["req"])
        sold = q if leg.get("dry_sold") else 0
        return {"bought": q, "cost": None, "sold": sold, "proceeds": None, "buys": [], "sells": []}
    buys = orders(ctx.conn, leg.get("buy") or [])
    sells = orders(ctx.conn, leg.get("sell") or [])
    assumed = bool(leg.get("assumed"))  # 옛 상태: 진입 체결가를 몰라 신호가로 본다
    return {
        "bought": int(leg["req"]) if assumed else sum(o["filled"] for o in buys),
        "cost": None if assumed else sum((o["filled_amt"] for o in buys), Decimal(0)),
        "sold": sum(o["filled"] for o in sells),
        "proceeds": sum((o["filled_amt"] for o in sells), Decimal(0)),
        "buys": [o for o in buys if live(o["status"])],
        "sells": [o for o in sells if live(o["status"])],
    }


def order_days(ctx: Any, market: str, pos: dict) -> list[date]:
    """체결을 다시 읽을 현지 날짜: 오늘 + 이월 전 주문일들(전일 주문의 체결·만료도 갱신)."""
    today = ctx.now().astimezone(MARKET_TZ[market]).date()
    days = {date.fromisoformat(x) for x in pos.get("order_days") or []}
    return sorted(days | {today})


def sync_fills(ctx: Any, market: str, pos: dict, synced: dict) -> None:
    """살아 있는 주문(또는 취소 뒤 재확인)이 있는 계좌만 체결을 다시 읽는다(틱당 계좌·날짜 1회).
    조회가 전부 성공해야 취소 뒤 재확인(`recheck`)을 지운다."""
    from trader.features.scalp import engine

    bmap = engine.brokers(ctx)
    known = {a.id for a in engine.accounts(ctx)}
    port = ctx.ports["stock_exec"]
    for acct, leg in legs(pos).items():
        if leg.get("dry"):
            continue
        v = leg_view(ctx, leg)
        if not v["buys"] and not v["sells"] and not leg.get("recheck"):
            continue
        if bmap.get(acct) is None or acct not in known:
            continue
        ok = True
        for d in order_days(ctx, market, pos):
            key = (acct, d.isoformat())
            if key not in synced:
                try:
                    res = port.sync_fills(
                        ctx.conn, account_id=acct, d=d, now=ctx.now, notifier=ctx.notifier
                    )
                    synced[key] = not res.get("error") and market not in (
                        res.get("failed_markets") or []
                    )
                except Exception as e:  # noqa: BLE001 - 다음 틱에 다시 읽는다
                    logger.warning("단타 체결 동기화 실패 %s: %s", acct, e)
                    synced[key] = False
            ok = ok and synced[key]
        if ok:
            leg.pop("recheck", None)  # 이번 조회로 취소 뒤 최종 체결을 확인했다


def cancel(
    ctx: Any,
    market: str,
    pos: dict,
    acct: str,
    leg: dict,
    o: dict,
    persist: Callable[[], None] = lambda: None,
) -> bool:
    """미체결 잔량 취소. 성공하면 그 다리에 `recheck`(side 목록)를 남긴다 — 취소 직전 체결이
    원장에 아직 없을 수 있어, 다음 틱 체결 동기화로 최종 수량을 확인하기 전에는 그 방향으로
    다리를 끝내거나(BUY) 잔량을 다시 팔지(SELL) 않는다."""
    from trader.features.scalp import engine

    if engine.brokers(ctx).get(acct) is None or not o.get("order_no"):
        return False  # 응답 없는(unknown) 주문은 번호가 없어 취소할 수 없다 → 체결 동기화 대기
    # 재확인 표시는 취소 **전에** 저장한다 — 취소 뒤 저장 전에 끊겨도 다음 틱이 최종 체결을 읽는다
    side = "buy" if int(o["id"]) in (leg.get("buy") or []) else "sell"
    leg["recheck"] = sorted({*(leg.get("recheck") or []), side})
    persist()
    try:
        res = ctx.ports["stock_exec"].cancel(
            ctx.conn,
            account_id=acct,
            order_id=int(o["id"]),
            order_no=str(o["order_no"]),
            market=market,
            exchange=pos.get("exchange"),
            dry_run=ctx.dry_run,
            reason="단타 청산 전 취소",
            notifier=ctx.notifier,
            now=ctx.now,
        )
    except (KisApiError, ValueError) as e:
        logger.warning("단타 미체결 취소 실패 %s %s #%s: %s", acct, pos["ticker"], o["order_no"], e)
        return False
    if res.get("ok") and not res.get("dry_run"):
        xledger.set_order_status(ctx.conn, int(o["id"]), "cancelled")
        event(
            ctx.state,
            ctx.now().astimezone(MARKET_TZ[market]).date(),
            market,
            "cancel",
            now=ctx.now(),
            ticker=pos["ticker"],
            account_id=acct,
            order_id=o["id"],
        )
        return True
    return False


def can_trade(ctx: Any, acct: str) -> bool:
    """실제 주문 다리를 건드려도 되는가: dry-run 아님·계좌 실효 모드 auto(일시정지는 보지 않는다 —
    청산 SELL 은 리스크 청산이라 일시정지 중에도 나간다).
    그 밖이면 취소도 매도도 하지 않는다 — 승인 요청을 틱마다 쌓거나 안전장치를 우회하지 않게(A8)."""
    from trader.features.scalp import engine

    acc = ctx.ports["accounts"]
    if ctx.dry_run:
        return False
    account = next((a for a in engine.accounts(ctx) if a.id == acct), None)
    return account is not None and acc.policy(ctx.conn, account)[0] == "auto"


def adopt_exit_orders(ctx: Any, pos: dict, acct: str, leg: dict) -> None:
    """청산 시작 뒤 이 계좌·종목에 낸 결정 없는 SELL(외부 수동 주문 제외) 중 다리에 없는 것을
    흡수한다 — 접수와 상태 저장 사이에 끊긴 경우 같은 잔량을 다시 팔지 않게."""
    since = pos.get("closing_at")  # 청산 시작은 주문 전에 저장된다
    if not since:
        return
    known = set(leg.get("sell") or [])
    for r in ctx.conn.execute(
        "SELECT id FROM orders WHERE account_id=? AND ticker=? AND side='SELL' "
        "AND decision_id IS NULL AND mode != 'external' AND created_at >= ? ORDER BY id",
        (acct, pos["ticker"], since),
    ):
        if int(r["id"]) not in known:
            leg.setdefault("sell", []).append(int(r["id"]))
            logger.warning("단타 청산 주문 흡수 %s %s #%s", acct, pos["ticker"], r["id"])


def adopt_entry_orders(ctx: Any, pos: dict) -> None:
    """임시 포지션(entering)의 진입 주문을 원장에서 찾아 다리로 만든다(진입 뒤 결정 없는 BUY).
    `origin='scalp'` 주문만 흡수한다(같은 종목의 rebalance·stop 등 다른 BUY 는 제외).
    계좌로 거르지 않는다(옛 결함 보존)."""
    out: dict[str, dict] = {}
    for r in ctx.conn.execute(
        "SELECT id, account_id, qty FROM orders WHERE ticker=? AND side='BUY' "
        "AND decision_id IS NULL AND mode != 'external' AND origin = 'scalp' "
        "AND created_at >= ? ORDER BY id",
        (pos["ticker"], pos["entry_at"]),
    ):
        leg = out.setdefault(r["account_id"], {"req": 0, "buy": [], "sell": []})
        leg["req"] += int(r["qty"])
        leg["buy"].append(int(r["id"]))
    pos |= {
        "legs": out,
        "qty": {a: leg["req"] for a, leg in out.items()},
        "status": "pending_entry",
    }
    logger.warning("단타 진입 주문 흡수 %s %s", pos["ticker"], sorted(out))


def px(v: Decimal) -> str:
    """체결 평균가 표기: 소수 4자리 이하, 지수 표기 없이."""
    return format(v.quantize(Decimal("0.0001")).normalize(), "f")


# --- 청산 단계 ---------------------------------------------------------------------------


def notify_paused_exit(
    ctx: Any, market: str, today: date, pos: dict, lg: dict[str, dict], results: list[dict]
) -> None:
    """일시정지 중 청산 SELL 이 실제로 접수되면 하루 1회(시장별) 알린다."""
    accepted = any(
        r.get("approved")
        and not r.get("error")
        and not (lg.get(r.get("account_id")) or {}).get("dry")
        and (r.get("order") or {}).get("order_id") is not None
        for r in results
    )
    if not accepted or not ctx.ports["accounts"].paused(ctx.conn):
        return
    key = f"scalp_exit_paused_notified:{market}:{today.isoformat()}"
    if control.get_flag(ctx.conn, key):
        return
    control.set_flag(ctx.conn, key, True)
    notify_quietly(
        ctx.notifier, messages.scalp_exit_while_paused(market=market, labels=[pos["ticker"]])
    )


def close_step(
    ctx: Any,
    *,
    market: str,
    today: date,
    pos: dict,
    price: Decimal,
    skip: frozenset[str],
    persist: Callable[[], None] = lambda: None,
) -> dict | None:
    """청산 중(closing) 포지션 한 걸음: 미체결 진입 BUY 취소 → 계좌별 체결 보유 수량만 SELL →
    전부 팔렸으면 체결가 기준 결과를 돌려준다(아직이면 None — 다음 틱 재시도). 진입 체결이
    하나도 없이 끝나면 {"void": True}."""
    from trader.features.scalp import engine

    cfg = ctx.settings.scalp
    lg = legs(pos)
    now = ctx.now()
    need: dict[str, int] = {}
    waiting = False
    for acct, leg in lg.items():
        if not leg.get("dry"):
            if not can_trade(ctx, acct):
                waiting = True  # dry-run·일시정지·auto 아닌 계좌: 취소·매도하지 않고 기다린다
                continue
            adopt_exit_orders(ctx, pos, acct, leg)
        v = leg_view(ctx, leg)
        for o in v["buys"]:
            if not cancel(ctx, market, pos, acct, leg, o, persist):
                waiting = True  # 취소되지 않은 진입은 더 체결될 수 있다 — 끝내지 않는다
        for o in v["sells"]:
            age = (now - datetime.fromisoformat(o["created_at"])).total_seconds()
            if o["order_type"] == "LIMIT" and age >= EXIT_REQUOTE_S:
                cancel(ctx, market, pos, acct, leg, o, persist)
            waiting = True  # 살아 있는 청산 주문: 체결(또는 취소 확인)을 기다린다
        if leg.get("recheck"):
            waiting = True  # 취소 뒤 최종 체결 확인 전에는 끝내지 않는다
        if v["sells"] or "sell" in (leg.get("recheck") or []):
            continue  # 청산 체결을 모르는 채 잔량을 다시 팔면 일반 보유분까지 판다
        rest = v["bought"] - v["sold"]
        if rest > 0:
            need[acct] = rest
    if need:
        dec = engine.decision("SELL", pos["ticker"], market, price, cfg, pos["exit_reason"])
        results = ctx.ports["stock_exec"].execute(
            ctx.conn,
            decision=dec,
            date=today,
            market=market,
            price=price,
            exchange=pos.get("exchange"),
            dry_run=ctx.dry_run,
            scalp_leg="exit",
            skip_accounts=skip | frozenset(a.id for a in engine.accounts(ctx) if a.id not in need),
            max_qty=need,
            trade_block=None,
            notifier=ctx.notifier,
            now=ctx.now,
            state=ctx.state,
        )
        pos.setdefault("exit_results", []).append(
            [{k: r.get(k) for k in ("account_id", "approved", "qty", "reasons")} for r in results]
        )
        for r in results:
            leg = lg.get(r.get("account_id"))
            if leg is None or r.get("error") or not r.get("approved"):
                continue
            order = r.get("order")
            if leg.get("dry"):
                if order is None and r.get("dry_run"):
                    leg["dry_sold"] = True
                    leg["dry_price"] = str(price)
                    need.pop(r["account_id"], None)
            elif order is not None and order.get("order_id") is not None:
                leg.setdefault("sell", []).append(int(order["order_id"]))
        notify_paused_exit(ctx, market, today, pos, lg, results)
        persist()  # 접수한 청산 주문 id 를 바로 남긴다(틱 중간에 끊겨도 다시 내지 않게)
        waiting = waiting or bool(need)
    if waiting:
        return None
    bought = sold = 0
    cost = proceeds = Decimal(0)
    for leg in lg.values():
        v = leg_view(ctx, leg)
        if v["cost"] is None:
            v["cost"] = Decimal(str(pos["entry_price"])) * v["bought"]
        if v["proceeds"] is None:
            v["proceeds"] = Decimal(str(leg.get("dry_price") or price)) * v["sold"]
        bought += v["bought"]
        sold += v["sold"]
        cost += v["cost"]
        proceeds += v["proceeds"]
    if bought == 0 or sold == 0:
        return {"void": True}
    entry_px = cost / bought
    exit_px = proceeds / sold
    return {
        "entry_fill": px(entry_px),
        "exit_price": px(exit_px),
        "filled": {a: leg_view(ctx, leg)["bought"] for a, leg in lg.items()},
        "ret": round(float(exit_px / entry_px - 1), 5),
    }


def check_exits(
    ctx: Any, *, market: str, today: date, st: dict, skip: frozenset[str], out: dict
) -> None:
    """보유·진입 대기 단타의 청산 점검(허용 판정과 무관하게 매 틱). 손절·익절·마감·최대 보유는
    파이썬이 즉시, 판정자 조언은 조기 청산만."""
    from trader.features.scalp import engine

    def persist() -> None:
        save_state(ctx.state, today, market, st)

    cfg = ctx.settings.scalp
    judge_on = cfg.judge and ctx.judge is not None
    now_t = engine.local_hms(ctx, market)
    synced: dict = {}
    for pos in list(st.get("open") or []):  # 끝난 포지션은 바로 빼서 중간 저장에 남지 않게
        if pos.get("status") == "entering":  # 진입 주문 뒤 저장 전에 끊겼다
            adopt_entry_orders(ctx, pos)
        legs(pos)
        if not ctx.dry_run:
            sync_fills(ctx, market, pos, synced)
        views = {a: leg_view(ctx, leg) for a, leg in pos["legs"].items()}
        bought = sum(v["bought"] for v in views.values())
        live_buys = any(v["buys"] for v in views.values())
        if pos.get("status") != "closing":
            if bought == 0 and not live_buys:  # 진입이 체결 없이 죽었다(거부·만료·취소)
                st["open"].remove(pos)
                event(ctx.state, today, market, "entry_void", now=ctx.now(), ticker=pos["ticker"])
                if ctx.judge is not None:
                    ctx.judge.discard(advice_key(market, pos))
                continue
            pos["status"] = "held" if bought > 0 else "pending_entry"
            if bought > 0:
                pos["filled"] = {a: v["bought"] for a, v in views.items()}
                costs = [v for v in views.values() if v["cost"] is not None and v["bought"] > 0]
                if costs:  # 청산 규칙은 실제 체결 평균가 기준(신호가 아님)
                    pos["entry_fill"] = px(
                        sum((v["cost"] for v in costs), Decimal(0))
                        / sum(v["bought"] for v in costs)
                    )
        try:
            q = engine.quote(ctx, market, pos["ticker"], pos.get("exchange"))
        except (KisApiError, ValueError) as e:
            logger.warning("단타 시세 실패 %s: %s", pos["ticker"], e)
            continue
        if pos.get("status") != "closing":
            reason = signals.exit_reason(
                entry_price=Decimal(str(pos.get("entry_fill") or pos["entry_price"])),
                entry_time=pos["entry_time"],
                price=q,
                now_time=now_t,
                cfg=engine.pos_cfg(cfg, pos),
                close_hm=ctx.close_hm,
            )
            if reason is None and judge_on:
                reason = engine.advice(ctx, market, today, pos, q)
            if reason is None:
                continue
            if ctx.judge is not None:
                ctx.judge.discard(advice_key(market, pos))
            pos |= {
                "status": "closing",
                "exit_reason": reason,
                "exit_time": now_t,
                "closing_at": ctx.now().isoformat(),
            }
            persist()
            event(
                ctx.state, today, market, "closing",
                now=ctx.now(), ticker=pos["ticker"], reason=reason,
            )  # fmt: skip
        sent = len(pos.get("exit_results") or [])
        res = close_step(
            ctx, market=market, today=today, pos=pos, price=q, skip=skip, persist=persist
        )
        if len(pos.get("exit_results") or []) > sent:  # 이 틱에 청산 SELL 을 냈다
            out["exit_orders"] = out.get("exit_orders", 0) + 1
        if res is None:
            continue
        st["open"].remove(pos)
        if res.get("void"):
            event(ctx.state, today, market, "entry_void", now=ctx.now(), ticker=pos["ticker"])
            continue
        reason = pos["exit_reason"]
        closed = {k: v for k, v in pos.items() if k not in ("status",)} | {
            "exit_price": res["exit_price"],
            "entry_fill": res["entry_fill"],
            "filled": res["filled"],
            "reason": reason,
            "ret": res["ret"],
        }
        st.setdefault("closed", []).append(closed)
        out["exits"].append({"ticker": pos["ticker"], "reason": reason, "ret": res["ret"]})
        event(ctx.state, today, market, "exit", now=ctx.now(), **closed)
        queue_note(
            st,
            leg="exit",
            label=engine.label(ctx, market, pos["ticker"]),
            price=res["exit_price"],
            reason=reason,
            ret=res["ret"],
        )


# --- 이월 --------------------------------------------------------------------------------


def carry_over(ctx: Any, market: str, today: date, st: dict) -> None:
    """지난 운영일 상태 파일의 미종결 단타(마감까지 못 판 잔량·unknown·일시정지)를 오늘로 옮겨
    곧바로 청산을 이어 간다. 매 틱 본다 — 오늘 파일을 먼저 쓰고 옛 파일을 비우므로 그 사이에
    끊겨도 다음 틱이 (종목, 진입 시각) 으로 중복 없이 마저 옮긴다."""
    from trader.features.scalp import engine

    have = {f"{p['ticker']}|{p.get('entry_at')}" for p in st.get("open") or []}
    have |= set(st.get("carried") or [])  # 이미 승계해 끝낸 것도(옛 파일이 다시 보여도) 건너뛴다
    for i in range(1, engine.CARRY_DAYS + 1):
        d = today - timedelta(days=i)
        p = state_dir(ctx.state, d) / f"{market}.json"
        if not p.exists():
            continue
        old = load_state(ctx.state, d, market)
        if not old.get("open"):
            continue
        for pos in old["open"]:
            pos["order_days"] = sorted({*(pos.get("order_days") or []), d.isoformat()})
            key = f"{pos['ticker']}|{pos.get('entry_at')}"
            if key in have:
                continue
            st.setdefault("carried", []).append(key)
            if pos.get("status") == "entering":  # 진입 접수 뒤 저장 전에 끊긴 채 넘어왔다
                adopt_entry_orders(ctx, pos)
            if pos.get("status") != "closing":
                pos |= {
                    "status": "closing",
                    "exit_reason": "carry_over",
                    "exit_time": engine.local_hms(ctx, market),
                    "closing_at": ctx.now().isoformat(),
                }
            pos["carried_from"] = d.isoformat()
            st["open"].append(pos)
            st.setdefault("done", []).append(pos["ticker"])
            event(
                ctx.state, today, market, "carry_over",
                now=ctx.now(), ticker=pos["ticker"], since=str(d),
            )  # fmt: skip
        old["open"] = []
        save_state(ctx.state, today, market, st)  # 오늘 파일을 먼저 — 끊겨도 잃지 않게
        save_state(ctx.state, d, market, old)
