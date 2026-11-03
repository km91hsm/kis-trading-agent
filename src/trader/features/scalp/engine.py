"""분 단위 단타 집행 — **모의 계좌 전용**, 기본 끔.

틱(60초)마다: ⓪ 열린 단타의 청산 점검(`exits` — 허용 판정과 무관, bear·금지·한도는 신규 진입만
막는다) → ① 허용 판정(설정·모의 계좌·paused·일일 손실 한도·bear 국면·전략가 RISK_OFF/
scalp_ok=false) → ② 판정 회수(지난 틱에 제출한 판정자 결과) → ③ 후보(오늘 `_scan` picks
+ 새 뉴스 재분석 종목 + 관심종목 − 보유·헤지·오늘 이미 거래) ≤ `max_universe` → ④ 1분봉 진입 규칙
(`signals.py`) → ⑤ 포트 `stock_exec.execute(scalp_leg=...)` 로 계좌 모드·dry_run·게이트를 그대로
지키며 주문(실전 계좌는 `skip_accounts`). 포지션은 **계좌별 다리**(진입·청산 주문 id)로 들고,
상태는 `state/scalp/<date>/<market>.json`, 사건은 같은 폴더 `events.jsonl`.
결정 행(`decisions`)은 만들지 않는다 — 오늘 LLM 결정과 같은 (date, market, ticker) 키를 덮기 때문.
레인이라 어떤 기능도 import 하지 않고 포트(`accounts`·`regime`·`scan`·`stock_exec`)만 쓴다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from trader.features.scalp import exits, messages, signals
from trader.features.scalp.judge import (
    ScalpJudge,
    bars_json,
    clamp_verdict,
    judge_context,
    judge_summary,
    limits,
)
from trader.features.scalp.schemas import ScalpAdvice, ScalpVerdict
from trader.features.scalp.settings import ScalpCfg
from trader.features.scalp.state import (
    advice_key,
    entry_key,
    event,
    load_state,
    queue_note,
    save_state,
    state_dir,
)
from trader.kernel.broker import tradability
from trader.kernel.broker.errors import KisApiError
from trader.kernel.broker.models import Candle
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.execution import costs
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import names
from trader.kernel.notify.telegram import Notifier, NullNotifier
from trader.kernel.platform import paths
from trader.kernel.session import run_store

logger = logging.getLogger("trader.scalp")

MINUTES_NEEDED = 60  # 틱마다 받는 1분봉 수 (진입 판정 25봉 + 여유)
CARRY_DAYS = 14  # 이만큼 거슬러 미종결 단타를 찾는다(주말·연휴 포함) — exits.carry_over


@dataclass
class ScalpContext:
    settings: Any
    conn: sqlite3.Connection
    ports: Mapping[str, Any]  # Boot.ports — accounts·regime·scan·stock_exec
    market_client: Any
    open_hm: str  # 그 시장 정규장 시작 "HH:MM" (현지)
    close_hm: str
    notifier: Notifier = field(default_factory=NullNotifier)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    state: Path | None = None
    dry_run: bool = False
    exchanges: dict[str, str] = field(default_factory=dict)  # US ticker → NAS|NYS|AMS
    judge: ScalpJudge | None = None  # 판정자(scalp.judge 일 때 잡이 넣는다)


# --- 포트 조회 ---------------------------------------------------------------------------


def accounts(ctx: ScalpContext) -> list:
    return list(ctx.ports["accounts"].accounts())


def brokers(ctx: ScalpContext) -> dict[str, Any]:
    """계좌 id → 브로커. 포트가 목록을 주면 `{b.account.id: b}` 로."""
    raw = ctx.ports["accounts"].brokers()
    return dict(raw) if isinstance(raw, dict) else {b.account.id: b for b in raw}


def label(ctx: ScalpContext, market: str, ticker: str) -> str:
    """알림용 '종목명(코드)' (원장·캐시만)."""
    return names.display(ctx.conn, (ctx.state or paths.state_dir()) / "cache", market, ticker)


# --- 허용 판정 -------------------------------------------------------------------------


def allowed(ctx: ScalpContext, market: str, today: date, st: dict) -> str | None:
    """단타 신규 진입을 하지 않을 이유(문자열) 또는 None. 킬스위치는 게이트(stock_exec 안)가
    본다."""
    cfg = ctx.settings.scalp
    if not cfg.enabled:
        return "scalp.enabled=false"
    if not any(a.env == "paper" and market in a.markets for a in accounts(ctx)):
        return "모의 계좌 없음 (단타는 모의 전용)"
    if ctx.ports["accounts"].paused(ctx.conn):
        return "일시정지"
    if st.get("halted"):
        return f"일일 손실 한도: {st['halted']}"
    regime = ctx.ports["regime"]
    hedge = regime.hedge_cfg()
    if hedge.enabled:
        if regime.state(ctx.conn, market) == "bear":
            return "하락 국면(bear)"
        view = regime.view_today(ctx.conn, market, today)
        if view and float(view.get("confidence") or 0) >= hedge.agent_min_confidence:
            if view.get("scalp_ok") is False:
                return "전략가 제안: 단타 금지(scalp_ok=false)"
            if view.get("stance") == "RISK_OFF":
                return "전략가 제안 RISK_OFF"
    return None  # 동시 보유 상한은 진입 루프가 본다(open + pending — 차단하면 판정 대기가 버려진다)


# --- 후보 -------------------------------------------------------------------------------


def candidates(ctx: ScalpContext, market: str, today: date, st: dict) -> list[dict]:
    """오늘 picks + 새 뉴스 재분석 종목 + 관심종목 − 보유·헤지·오늘 거래분. {ticker, exchange}.
    거래소: 출처가 준 값 → `ctx.exchanges` → `_scan.analyze_targets` → 원장 `ticker_exchanges`
    (US 만). US 는 거래소를 모르면 뺀다."""
    cfg = ctx.settings.scalp
    mdir = run_store.market_dir(today, market, ctx.state)
    scan = run_store.read_checkpoint(mdir, "_scan") or {}
    targets = scan.get("analyze_targets") or []
    target_ex = {t.get("ticker"): t.get("exchange") for t in targets if t.get("exchange")}
    out: list[dict] = []
    seen: set[str] = set()

    def add(t: str | None, ex: str | None = None) -> None:
        if not t or t in seen:
            return
        seen.add(t)
        out.append({"ticker": t, "exchange": ex or ctx.exchanges.get(t) or target_ex.get(t)})

    for p in scan.get("picks") or []:
        add(p.get("ticker"))
    for t in targets:
        if t.get("source") == "pick":
            add(t.get("ticker"), t.get("exchange"))
    lo, hi = xledger.day_bounds(today, market)
    for r in ctx.conn.execute(
        "SELECT params FROM job_requests WHERE source='news' AND kind='analyze_ticker' "
        "AND requested_at >= ? AND requested_at < ?",
        (lo, hi),
    ).fetchall():
        params = json.loads(r["params"]) if r["params"] else {}
        if params.get("market") == market:
            add(params.get("ticker"), params.get("exchange"))
    for w in ctx.ports["scan"].watchlist(ctx.conn, market):
        add(w)
    held: set[str] = set()
    bmap = brokers(ctx)
    for a in accounts(ctx):
        b = bmap.get(a.id)
        if b is None or market not in a.markets:
            continue
        try:
            held |= {p.ticker for p in b.get_positions(market)}
        except (KisApiError, ValueError) as e:
            logger.warning("단타 보유 조회 실패 %s: %s", a.id, e)
    hedge = ctx.ports["regime"].hedge_tickers(market)
    open_t = {o["ticker"] for o in st.get("open") or []}
    done = set(st.get("done") or []) | set(st.get("pending") or {})
    picked = [
        c
        for c in out
        if c["ticker"] not in held - open_t
        and c["ticker"] not in hedge
        and c["ticker"] not in done
        and c["ticker"] not in open_t
    ]
    if market == "US":
        for c in picked:
            if not c.get("exchange"):
                c["exchange"] = xledger.exchange_of(ctx.conn, market, c["ticker"])
        picked = [c for c in picked if c.get("exchange")]
    return picked[: cfg.max_universe]


# --- 시세·보조 -------------------------------------------------------------------------


def local_hms(ctx: ScalpContext, market: str) -> str:
    return ctx.now().astimezone(MARKET_TZ[market]).strftime("%H%M%S")


def minutes(ctx: ScalpContext, market: str, c: dict) -> list[Candle]:
    if market == "KR":
        return ctx.market_client.get_minute_kr(c["ticker"], count=MINUTES_NEEDED)
    return ctx.market_client.get_minute_us(c["ticker"], c["exchange"], count=MINUTES_NEEDED)


def quote(ctx: ScalpContext, market: str, ticker: str, exchange: str | None) -> Decimal:
    return (
        ctx.market_client.get_price_kr(ticker).price
        if market == "KR"
        else ctx.market_client.get_price_us(ticker, exchange).price
    )


def trade_block(ctx: ScalpContext, market: str, ticker: str) -> str | None:
    """단타 진입 전 매매 제한 종목(관리·경고·정지) — 국내만, 조회 실패는 None."""
    if market != "KR" or ctx.market_client is None:
        return None
    try:
        return tradability.block_reason(ctx.market_client.get_price_kr(ticker))
    except Exception:  # noqa: BLE001 - 판정 보조: 실패하면 게이트의 다른 규칙만
        return None


def skip_accounts(ctx: ScalpContext, market: str) -> frozenset[str]:
    """단타는 모의 전용: 실전 계좌·그 시장이 아닌 계좌는 매 주문에서 건너뛴다(A8)."""
    return frozenset(a.id for a in accounts(ctx) if a.env != "paper" or market not in a.markets)


def decision(
    action: str,
    ticker: str,
    market: str,
    price: Decimal,
    cfg: ScalpCfg,
    why: str,
    *,
    weight_mult: float = 1.0,
) -> dict:
    """포트 `stock_exec` 에 넘기는 Decision 모양 dict. 진입은 지정가, 청산은 KR 시장가·US 지정가."""
    limit = action == "BUY" or market == "US"
    return {
        "ticker": ticker,
        "action": action,
        "target_weight": cfg.max_weight_pct * weight_mult if action == "BUY" else 0.0,
        "order_type": "LIMIT" if limit else "MARKET",
        "limit_price": float(price) if limit else None,
        "stop_loss": None,
        "take_profit": None,
        "horizon_days": 1,
        "confidence": 1.0,
        "rationale": f"분 단위 단타({'진입' if action == 'BUY' else '청산'}): {why}"[:500],
        "invalidation_conditions": [],
    }


def entry_legs(results: list[dict]) -> dict[str, dict]:
    """계좌별 진입 다리: 접수(accepted·unknown) 주문 id 와 요청 수량. dry_run 승인은 주문 없이
    체결로 본다(`dry`). 거부·실패 계좌는 다리가 없다 — 청산도 그 계좌를 건너뛴다."""
    out: dict[str, dict] = {}
    for r in results:
        qty = int(r.get("qty") or 0)
        if r.get("error") or not r.get("approved") or qty <= 0:
            continue
        order = r.get("order")
        if order is None:
            if r.get("dry_run"):
                out[r["account_id"]] = {"req": qty, "dry": True, "buy": [], "sell": []}
            continue
        if order.get("status") in ("accepted", "unknown") and order.get("order_id") is not None:
            out[r["account_id"]] = {"req": qty, "buy": [int(order["order_id"])], "sell": []}
    return out


def pos_cfg(cfg: ScalpCfg, pos: dict) -> ScalpCfg:
    """판정자가 정한 포지션별 청산 파라미터를 설정 위에 덮는다."""
    over = {k: pos[k] for k in ("take_profit_pct", "stop_pct", "max_hold_min") if k in pos}
    return cfg.model_copy(update=over) if over else cfg


# --- 진입 -------------------------------------------------------------------------------


def enter(
    ctx: ScalpContext,
    *,
    market: str,
    today: date,
    st: dict,
    c: dict,
    price: Decimal,
    time_hms: str,
    reason: str,
    skip: frozenset[str],
    weight_mult: float = 1.0,
    params: dict | None = None,
) -> dict | None:
    cfg = ctx.settings.scalp
    dec = decision("BUY", c["ticker"], market, price, cfg, reason, weight_mult=weight_mult)
    # 하루 종목당 1회(거부돼도) + 임시 포지션(entering)을 주문 전에 남긴다 — 접수 뒤 저장 전에
    # 끊기면 다음 틱이 원장의 진입 주문을 흡수한다(`exits.adopt_entry_orders`), 다시 사지 않는다
    st.setdefault("done", []).append(c["ticker"])
    pos = {
        "ticker": c["ticker"],
        "exchange": c.get("exchange"),
        "weight": cfg.max_weight_pct * weight_mult,  # 일일 손실 계산이 실제 비중을 쓰게
        "entry_price": str(price),
        "entry_time": time_hms,
        "entry_at": ctx.now().isoformat(),
        "qty": {},
        "legs": {},
        "status": "entering",
        "reason": reason,
        "dry_run": ctx.dry_run,
        **(params or {}),
    }
    st["open"].append(pos)
    save_state(ctx.state, today, market, st)
    results = ctx.ports["stock_exec"].execute(
        ctx.conn,
        decision=dec,
        date=today,
        market=market,
        price=price,
        exchange=c.get("exchange"),
        dry_run=ctx.dry_run,
        scalp_leg="entry",
        skip_accounts=skip,
        max_qty=None,
        trade_block=trade_block(ctx, market, c["ticker"]),
        notifier=ctx.notifier,
        now=ctx.now,
        state=ctx.state,
    )
    legs = entry_legs(results)
    if not legs:
        st["open"].remove(pos)
        save_state(ctx.state, today, market, st)
        reasons = [r.get("reasons") for r in results]
        event(
            ctx.state, today, market, "entry_rejected",
            now=ctx.now(), ticker=c["ticker"], reasons=reasons,
        )  # fmt: skip
        return None
    pos |= {
        "qty": {a: leg["req"] for a, leg in legs.items()},  # 요청 수량(표시용)
        "legs": legs,
        "status": "held" if ctx.dry_run else "pending_entry",
    }
    save_state(ctx.state, today, market, st)
    event(ctx.state, today, market, "entry", now=ctx.now(), **pos)
    queue_note(
        st, leg="entry", label=label(ctx, market, c["ticker"]), price=str(price), reason=reason
    )
    return pos


# --- 판정 회수 ---------------------------------------------------------------------------


def settle_pending(
    ctx: ScalpContext, *, market: str, today: date, st: dict, skip: frozenset[str]
) -> list[dict]:
    """지난 틱에 제출한 진입 판정을 회수한다. 판정 없음(재시작·실패·상한·감속)·시간 초과·마감
    컷오프·가격 이탈·NO_GO 는 진입하지 않고 그날 그 종목은 끝(done)."""
    cfg = ctx.settings.scalp
    entered: list[dict] = []
    pend: dict = st.setdefault("pending", {})
    now_m = signals.hm_minutes(local_hms(ctx, market))
    cutoff = now_m >= signals.hm_minutes(ctx.close_hm) - cfg.close_before_min
    for t, p in list(pend.items()):
        key = entry_key(market, today, t)
        age = (ctx.now() - datetime.fromisoformat(p["submitted_at"])).total_seconds()
        v = ctx.judge.poll(key) if ctx.judge is not None else None
        if v == "pending" and age <= cfg.judge_timeout_s and not cutoff:
            continue
        if v == "pending" or age > cfg.judge_timeout_s:
            # 제출 시각 기준 시한: 대기열에서 늦게 끝난 판정도 버린다(그 사이 시장이 움직였다)
            if ctx.judge is not None:
                ctx.judge.discard(key)
            v, why = None, f"판정 시간 초과({cfg.judge_timeout_s:.0f}초)"
        elif cutoff:
            v, why = None, "마감 전 진입 금지 시각 — 판정을 쓰지 않음"
        elif v is None:
            why = "판정 없음(재시작·실패·상한)"
        else:
            why = None
        base = {
            "ticker": t,
            "signal_price": p["signal_price"],
            "signal_time": p["signal_time"],
            "exchange": p.get("exchange"),
        }
        pend.pop(t, None)
        st.setdefault("done", []).append(t)
        if v is None:
            event(ctx.state, today, market, "judge", now=ctx.now(), go=None, why=why, **base)
            continue
        event(
            ctx.state, today, market, "judge", now=ctx.now(),
            go=v.go, size=v.size, confidence=v.confidence, why=v.reason, **base,
        )  # fmt: skip
        if not v.go:
            continue
        try:
            q = quote(ctx, market, t, p.get("exchange"))
        except (KisApiError, ValueError) as e:
            logger.warning("단타 판정 뒤 시세 실패 %s: %s", t, e)
            continue
        sig = Decimal(str(p["signal_price"]))
        if sig > 0 and abs(q / sig - 1) > Decimal(str(cfg.judge_max_drift_pct)):
            event(
                ctx.state, today, market, "judge_drift",
                now=ctx.now(), ticker=t, signal=str(sig), price=str(q),
            )  # fmt: skip
            continue
        st["done"].remove(t)  # enter 가 다시 넣는다
        pos = enter(
            ctx,
            market=market,
            today=today,
            st=st,
            c={"ticker": t, "exchange": p.get("exchange")},
            price=q,
            time_hms=local_hms(ctx, market),
            reason=f"{p['reason']} · 판정 GO({v.size}): {v.reason}"[:300],
            skip=skip,
            weight_mult=0.5 if v.size == "half" else 1.0,
            params=clamp_verdict(v, cfg) | {"judged": True},
        )
        if pos is not None:
            entered.append(pos)
    return entered


# --- 보유 조언 (exits.check_exits 가 부른다) ----------------------------------------------


def advice(ctx: ScalpContext, market: str, today: date, pos: dict, q: Decimal) -> str | None:
    """판정자 보유 조언: 지난 조언이 exit 면 "judge_exit", 아니면 주기가 됐을 때 새로 묻는다."""
    cfg = ctx.settings.scalp
    key = advice_key(market, pos)
    adv = ctx.judge.poll(key)
    if adv not in (None, "pending") and getattr(adv, "action", None) == "exit":
        event(
            ctx.state, today, market, "advice",
            now=ctx.now(), ticker=pos["ticker"], why=adv.reason,
        )  # fmt: skip
        return "judge_exit"
    if adv == "pending":
        return None
    last = pos.get("advice_at") or pos.get("entry_at")
    due = last is None or ctx.now() - datetime.fromisoformat(last) >= timedelta(
        minutes=cfg.judge_hold_every_min
    )
    if due:
        pos["advice_at"] = ctx.now().isoformat()
        payload = {
            "kind": "hold",
            "market": market,
            "ticker": pos["ticker"],
            "entry": {"price": pos["entry_price"], "time": pos["entry_time"]},
            "price": str(q),
            "limits": limits(pos_cfg(cfg, pos)),
        }
        try:
            payload["bars"] = bars_json(minutes(ctx, market, pos))
        except (KisApiError, ValueError):
            payload["bars"] = []
        payload["news"] = judge_context(
            ctx.conn, ctx.ports, ctx.market_client, market=market, ticker=pos["ticker"],
            exchange=pos.get("exchange"), today=run_store.run_date_for(market, ctx.now()),
        )["news"]  # fmt: skip
        ctx.judge.submit(key, payload, ScalpAdvice)
    return None


# --- 알림 묶음 ---------------------------------------------------------------------------


def send_digest(
    notifier: Notifier,
    market: str,
    st: dict,
    *,
    now: datetime,
    every_min: int,
    dry_run: bool,
    force: bool,
) -> bool:
    """outbox 를 `messages.scalp_digest` 한 통으로 보낸다. 그날 첫 묶음은 바로, 그 뒤는
    `digest_every_min` 마다, 중단·마감 청산·허용 해제 때는 즉시(force). 보내지면 비우고 True,
    실패하면 남겨 다음 틱에 다시 보낸다."""
    box = st.get("outbox") or []
    if not box:
        return False
    last = st.get("digest_at")
    if not force and last and (now - datetime.fromisoformat(last)).total_seconds() < every_min * 60:
        return False
    try:
        sent = notifier.send(messages.scalp_digest(market=market, items=box, dry_run=dry_run))
    except Exception:  # noqa: BLE001 - 알림 실패가 단타 상태를 바꾸지 않는다
        logger.warning("단타 묶음 알림 실패", exc_info=True)
        sent = False
    if sent is False:  # 보관함 없는 notifier 만 실패를 False 로 — 묶음을 남겨 다음 틱에 다시
        return False
    st["outbox"] = []
    st["digest_at"] = now.isoformat()
    return True


def flush_digest(ctx: ScalpContext, market: str, st: dict, *, force: bool = False) -> None:
    send_digest(
        ctx.notifier,
        market,
        st,
        now=ctx.now(),
        every_min=ctx.settings.scalp.digest_every_min,
        dry_run=ctx.dry_run,
        force=force,
    )


def flush_outbox(
    state: Path | None,
    today: date,
    market: str,
    *,
    notifier: Notifier,
    now: datetime,
    settings: Any,
    dry_run: bool,
) -> bool:
    """tick 이 돌지 않는 틱(단타 꺼짐·일시정지·창 밖 — 잡이 tick 전에 건너뜀)에 모아 둔 묶음을
    보낸다(그날 상태 파일에 갇히면 다음 날엔 다른 파일을 읽어 영영 안 간다)."""
    st = load_state(state, today, market)
    if not st.get("outbox"):
        return False
    every = settings.scalp.digest_every_min
    if not send_digest(notifier, market, st, now=now, every_min=every, dry_run=dry_run, force=True):
        return False
    save_state(state, today, market, st)
    return True


# --- 틱 ---------------------------------------------------------------------------------


def _judge_submit(
    ctx: ScalpContext, *, market: str, today: date, st: dict, c: dict, bars: list, e: Any
) -> None:
    """판정자에게 묻고 다음 틱에 회수 — 이번 틱엔 진입하지 않는다."""
    payload = {
        "kind": "entry",
        "market": market,
        "ticker": c["ticker"],
        "name": label(ctx, market, c["ticker"]),
        "signal": {"price": str(e.price), "time": e.time, "reason": e.reason()},
        "bars": bars_json(bars),
        "limits": limits(ctx.settings.scalp),
    } | judge_context(
        ctx.conn, ctx.ports, ctx.market_client, market=market, ticker=c["ticker"],
        exchange=c.get("exchange"), today=run_store.run_date_for(market, ctx.now()),
    )  # fmt: skip
    ctx.judge.submit(entry_key(market, today, c["ticker"]), payload, ScalpVerdict)
    st.setdefault("pending", {})[c["ticker"]] = {
        "signal_price": str(e.price),
        "signal_time": e.time,
        "reason": e.reason(),
        "exchange": c.get("exchange"),
        "submitted_at": ctx.now().isoformat(),
    }
    event(
        ctx.state, today, market, "judge_submit",
        now=ctx.now(), ticker=c["ticker"], price=str(e.price),
    )  # fmt: skip


def tick(ctx: ScalpContext, *, market: str, today: date) -> dict:
    """1틱: 청산 → (판정 회수) → 진입. 반환 {entries, exits, candidates, errors[, skipped]}.
    청산은 허용 판정(bear·전략가 금지·일일 손실·일시정지)보다 먼저 돈다 — 그 제한은 신규 진입만
    막는다. 일시정지면 주문이 나가지 않아 포지션은 closing 으로 남는다. 후보 하나의 분봉·주문
    예외는 그 종목만 `errors` 에 남기고 다음 후보로 간다."""
    cfg = ctx.settings.scalp
    st = load_state(ctx.state, today, market)
    exits.carry_over(ctx, market, today, st)
    skip = skip_accounts(ctx, market)
    out: dict[str, Any] = {"entries": [], "exits": [], "candidates": 0, "errors": []}
    had_state = bool(st.get("open") or st.get("pending"))
    if st.get("open"):
        exits.check_exits(ctx, market=market, today=today, st=st, skip=skip, out=out)
    # 일일 손실 한도: 청산된 거래의 (수익률 × 비중) 합
    realized = sum(
        float(c.get("ret") or 0) * float(c.get("weight") or cfg.max_weight_pct)
        for c in st.get("closed") or []
    )
    if realized <= -cfg.daily_stop_pct and not st.get("halted"):
        st["halted"] = f"실현 {realized:+.2%} ≤ -{cfg.daily_stop_pct:.2%}"
        had_state = True
        ctx.notifier.send(messages.scalp(leg="halt", market=market, label="", reason=st["halted"]))
        event(ctx.state, today, market, "halt", now=ctx.now(), realized=realized)
    why = allowed(ctx, market, today, st)
    if why:  # 신규 진입만 막는다: 판정 대기 신호도 버린다(그날 그 종목은 끝)
        if st.get("pending"):
            for t in st["pending"]:
                if ctx.judge is not None:
                    ctx.judge.discard(entry_key(market, today, t))
            st["done"] = [*st.get("done", []), *st["pending"]]
            st["pending"] = {}
        if st.get("outbox"):  # 진입이 막히면 다음 묶음 시점이 없을 수 있다 — 청산 묶음을 지금
            flush_digest(ctx, market, st, force=True)
            had_state = True
        if had_state:
            save_state(ctx.state, today, market, st)
        return out | {"skipped": why}
    judge_on = cfg.judge and ctx.judge is not None
    # --- 판정 회수 (지난 틱 제출분) ---
    if st.get("pending"):
        for pos in settle_pending(ctx, market=market, today=today, st=st, skip=skip):
            out["entries"].append(
                {"ticker": pos["ticker"], "price": pos["entry_price"], "qty": pos["qty"]}
            )
    # --- 진입 ---
    if len(st["open"]) + len(st.get("pending") or {}) < cfg.max_positions:
        cands = candidates(ctx, market, today, st)
        out["candidates"] = len(cands)
        for c in cands:
            if len(st["open"]) + len(st.get("pending") or {}) >= cfg.max_positions:
                break
            try:
                bars = minutes(ctx, market, c)
                e = signals.entry_signal(bars, cfg, open_hm=ctx.open_hm, close_hm=ctx.close_hm)
                if e is None:
                    continue
                if judge_on:
                    _judge_submit(ctx, market=market, today=today, st=st, c=c, bars=bars, e=e)
                    continue
                pos = enter(
                    ctx,
                    market=market,
                    today=today,
                    st=st,
                    c=c,
                    price=e.price,
                    time_hms=e.time,
                    reason=e.reason(),
                    skip=skip,
                )
            except Exception as err:  # noqa: BLE001 - 종목 하나의 실패가 틱을 멈추지 않는다
                logger.warning("단타 후보 처리 실패 %s: %s", c["ticker"], err)
                out["errors"].append({"ticker": c["ticker"], "error": str(err)})
                continue
            if pos is not None:
                out["entries"].append(
                    {"ticker": c["ticker"], "price": str(e.price), "qty": pos["qty"]}
                )
    now_m = signals.hm_minutes(local_hms(ctx, market))
    closing = now_m >= signals.hm_minutes(ctx.close_hm) - cfg.close_before_min
    flush_digest(ctx, market, st, force=bool(st.get("halted")) or closing)
    save_state(ctx.state, today, market, st)
    return out


# --- 리포트 ------------------------------------------------------------------------------


def report(
    state: Path | None,
    *,
    market: str,
    days: int = 5,
    today: date | None = None,
    judge: dict | None = None,
) -> dict:
    """최근 운영일의 단타 상태 파일을 모아 승률·사유별 집계. judge(={cfg, close_hm, minutes_fn})를
    주면 판정자 GO vs NO_GO 사후 비교(`judge_summary`)를 덧붙인다."""
    today = today or datetime.now(UTC).date()
    trades: list[dict] = []
    per_day = []
    for i in range(days * 2):
        d = today - timedelta(days=i)
        p = state_dir(state, d) / f"{market}.json"
        if not p.exists():
            continue
        st = json.loads(p.read_text(encoding="utf-8"))
        closed = st.get("closed") or []
        trades += closed
        per_day.append({"date": d.isoformat(), "trades": len(closed), "halted": st.get("halted")})
        if len(per_day) >= days:
            break
    summary = signals.summarize(trades, round_trip_bp=costs.round_trip_bp(market))
    out = {"market": market, "days": per_day, "summary": summary}
    if judge is not None:
        out["judge"] = judge_summary(
            state,
            market=market,
            days=[date.fromisoformat(x["date"]) for x in per_day],
            today=today,
            **judge,
        )
    return out
