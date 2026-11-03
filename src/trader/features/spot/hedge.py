"""국면 헤지(Phase 17 H): bear 면 인버스 ETF 를 계좌 NAV 의 `hedge.max_weight_pct` 만큼 들고,
벗어나면 전량 판다 — spot 의 파이썬 의도 생산자.

LLM 은 관여하지 않는다. 결정은 파이썬이 `Decision` 으로 만들고(`decisions.source='hedge'`), 주문은
`executor.execute(is_hedge=True)` 로 계좌마다 모드(off/approve/auto·LIVE 확인·paused·dry_run)를
그대로 지킨다. 헤지 설정·국면·시장 세션 제안·상품·비중 clamp 는 전부 `ctx.ports["regime"]`(덕
타이핑, 레인은 regime 을 import 하지 않는다)로 받고, 포트가 없거나 `hedge.enabled=false` 면
아무것도 하지 않는다. 운영일에 1회(`control.hedge_done:<market>:<date>`)만 돌며 데몬
`{market}_monitor` 틱이 `hedge_tick` 으로 부른다(개장 뒤 실시간 시세). 보유 중 상품(−1x/−2x)
교체는 기본으로 하지 않는다 — 진입 때 고른 것을 해제까지 든다. Phase 21 `switch_instruments=true`
면 bear 유지 중 판정(count≥3 ∧ leverage)과 보유 상품이 `switch_streak_days` 연속 어긋날 때 전량
매도하고(action `switch`) 다음 운영일 진입 규칙이 새 상품을 산다(같은 날 매도·매수를 함께 내지
않는다 — 매도 현금을 확인할 수 없다).

Phase 18: 시장 세션의 제안(`view_today`, HedgeProposal)이 오늘 것으로 있으면 **bear 진입 비중**을
`[min_weight_pct, max_weight_pct]` 안에서 조절하고(`clamp_weight`) 레버리지를 거부
(`leveraged=false`)할 수 있다. 진입 여부(bear)·해제는 제안과 무관하게 국면 규칙이 정한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from trader.features.spot import executor, labels
from trader.features.spot import ledger as spot_ledger
from trader.features.spot import messages as spot_messages
from trader.features.spot.schemas import Decision
from trader.kernel.broker.errors import KisApiError
from trader.kernel.execution import core
from trader.kernel.ledger import control
from trader.kernel.notify import messages
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.session.run_store import run_date_for

logger = logging.getLogger("trader.spot")

HORIZON_DAYS = 20
MAX_RETRIES = 3  # 실패한 계좌의 재시도 상한(운영일 안, monitor 틱마다 1회)


@dataclass(frozen=True)
class _Inst:
    """설정에 없는 보유 헤지 종목(옛 `HedgeInstrument(symbol=t)`) — 해제·교체 매도용."""

    symbol: str
    exchange: str | None = None


def done_key(market: str, today: date) -> str:
    return f"hedge_done:{market}:{today.isoformat()}"


def switch_key(market: str) -> str:
    return f"hedge_switch_streak:{market}"


def _notify(ctx: executor.ExecContext, text: str) -> None:
    core.notify_quietly(ctx.notifier, text)


def _known(cfg: Any, market: str, symbol: str) -> Any | None:
    """설정 `hedge.instruments[market]` 에서 종목 코드로 상품을 찾는다(없으면 None)."""
    return next((i for i in cfg.instruments.get(market, {}).values() if i.symbol == symbol), None)


def _switch_due(conn, market: str, today: date, cfg: Any, *, held: set[str], want: Any) -> Any:
    """보유 상품이 원하는 상품과 다른 날을 센다(운영일마다 1). streak ≥ switch_streak_days 면 원하는
    상품을 돌려주고(교체 매도 실행), 일치하면 플래그를 지운다."""
    key = switch_key(market)
    if want is None or held == {want.symbol}:
        if control.get_flag(conn, key) is not None:
            control.set_flag(conn, key, None)
        return None
    prev = control.get_flag(conn, key) or {}
    days = int(prev.get("days") or 0)
    if prev.get("want") != want.symbol:
        days = 0
    if prev.get("date") != today.isoformat():
        days += 1
    control.set_flag(conn, key, {"days": days, "want": want.symbol, "date": today.isoformat()})
    return want if days >= cfg.switch_streak_days else None


def hedge_decision(
    action: str,
    ticker: str,
    *,
    market: str,
    weight: float,
    price: Decimal,
    state: dict,
    view: dict | None = None,
) -> Decision:
    """파이썬이 만든 헤지 결정. KR 은 시장가, US 는 현재가 지정가(모의 서버 제약)."""
    sig = state.get("signals") or {}
    keys = ("ma_below", "momentum_weak", "vol_high", "flow_weak", "agent_risk_off")
    on = [k for k in keys if sig.get(k)]
    note = ""
    if view and action == "BUY":
        pct = float(view.get("hedge_weight") or 0) * 100
        lev = "" if view.get("leveraged", True) else " 레버리지 거부"
        note = f"; 시장 세션 제안 {view.get('stance')} {pct:.0f}%{lev}"
    return Decision(
        ticker=ticker,
        action=action,  # type: ignore[arg-type]
        target_weight=weight if action == "BUY" else 0.0,
        order_type="MARKET" if market == "KR" else "LIMIT",
        limit_price=None if market == "KR" else float(price),
        stop_loss=None,
        take_profit=None,
        horizon_days=HORIZON_DAYS,
        confidence=1.0,
        rationale=(
            f"국면 {state.get('state')}({state.get('count')}/3: {', '.join(on) or '-'}) 헤지 "
            f"{'진입' if action == 'BUY' else '해제'} — 파이썬 규칙{note}"
        ),
        invalidation_conditions=["국면이 bear 를 벗어나면 전량 매도(파이썬)"],
    )


def _held_hedges(
    ctx: executor.ExecContext, market: str, tickers: frozenset[str]
) -> tuple[dict[str, set[str]], set[str]]:
    """계좌별 보유 헤지 종목 + 잔고 조회에 실패한 계좌(보유를 몰라 이번엔 건너뛴다)."""
    held: dict[str, set[str]] = {}
    failed: set[str] = set()
    for a in ctx.accounts:
        if market not in a.markets:
            continue
        b = ctx.brokers.get(a.id)
        if b is None:
            continue
        try:
            held[a.id] = {
                p.ticker for p in b.get_positions(market) if p.ticker in tickers and p.qty > 0
            }
        except (KisApiError, ValueError) as e:
            logger.warning("헤지 보유 조회 실패 %s: %s", a.id, e)
            failed.add(a.id)
    return held, failed


def _failed_accounts(results: list[dict]) -> set[str]:
    """주문이 나가야 했는데 못 나간 계좌(시세·잔고·주문 실패).
    거부 판정·off·dry-run 은 실패가 아니다."""
    out: set[str] = set()
    for r in results:
        if "error" in r and r.get("account_id"):
            out.add(str(r["account_id"]))
        o = r.get("order")
        if o and not o.get("accepted") and o.get("status") != "unknown":
            out.add(str(r["account_id"]))
    return out


def _leveraged(port: Any, state: dict, view: dict | None) -> bool:
    """−2x 는 정량 3지표가 모두 켜졌을 때만(전략가·수급 보조 신호는 세지 않는다, 검토 O14), 그리고
    오늘 제안이 레버리지를 거부하지 않을 때."""
    return port.core_count(state) >= 3 and (view is None or bool(view.get("leveraged", True)))


def rebalance(
    ctx: executor.ExecContext,
    *,
    market: str,
    today: date,
    quote_fn: Callable[[Any], Decimal],
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """운영일 1회. 반환 {state, action: enter|exit|switch|deferred|none, ticker, results|skipped}.
    `ctx.ports["regime"]` 가 없거나 헤지가 꺼져 있으면 `{"skipped": "hedge.enabled=false"}`."""
    port = ctx.ports.get("regime")
    cfg = port.hedge_cfg() if port is not None else None
    if cfg is None or not cfg.enabled:
        return {"skipped": "hedge.enabled=false"}
    key = done_key(market, today)
    prev = control.get_flag(ctx.conn, key) if not force else None
    if prev and not isinstance(prev, dict):
        return {"skipped": "오늘 이미 실행"}
    retry = int((prev or {}).get("retry") or 0)
    done_accounts = set((prev or {}).get("done") or [])
    if prev and (not (prev.get("pending")) or retry >= MAX_RETRIES):
        return {"skipped": "오늘 이미 실행"}
    state = port.load(ctx.conn, market) or {}
    st = str(state.get("state") or "bull")
    held_by, unknown = _held_hedges(ctx, market, port.hedge_tickers(market))
    held = set().union(*held_by.values()) if held_by else set()
    # 계좌별로 본다: 보유한 계좌는 진입하지 않고, 보유하지 않은 계좌는 해제하지 않는다.
    # 오늘 이미 성공한 계좌·잔고를 모르는 계좌는 건너뛴다
    skip_enter = frozenset(done_accounts | unknown | {a for a, h in held_by.items() if h})
    view = port.view_today(ctx.conn, market, today)
    out: dict[str, Any] = {
        "market": market,
        "state": st,
        "held": sorted(held),
        "held_by": {a: sorted(h) for a, h in held_by.items()},
        "action": "none",
        "view": (
            {k: view.get(k) for k in ("stance", "hedge_weight", "leveraged")} if view else None
        ),
    }
    deferred = _risk_on_deferral(state, view, cfg)
    if st == "bear" and not held and deferred:
        # 전략가 RISK_ON(확신 ≥ 기준)이고 지표가 경계값(bear_min_signals)이면 오늘 진입을 보류한다.
        # 3/3 은 보류 불가. 해제 규칙은 그대로(제안이 팔게 하지 않는다)
        out.update(action="deferred", reason=deferred)
        if not dry_run:
            control.set_flag(
                ctx.conn, key, {"state": st, "action": "deferred", "done": [], "pending": []}
            )
            _notify(ctx, messages.event("헤지 보류", f"{market} {deferred}"))
        return out
    run = _Runner(ctx, market, today, cfg, state, quote_fn, dry_run)
    if st == "bear" and not held:
        inst = port.instrument(market, leveraged=_leveraged(port, state, view))
        if inst is None:
            out["error"] = f"{market} 헤지 상품 미설정"
        else:
            weight = port.clamp_weight(view)
            out.update(action="enter", ticker=inst.symbol, weight=weight)
            out["results"] = run("BUY", inst, weight, view, skip_accounts=skip_enter)
    elif st == "bear" and any(
        not h for a, h in held_by.items() if a not in done_accounts | unknown
    ):
        # 일부 계좌만 들고 있다(어제 한 계좌가 실패): 없는 계좌만 진입
        inst = port.instrument(market, leveraged=False)
        for t in sorted(held):
            inst = _known(cfg, market, t) or inst
        if inst is not None:
            weight = port.clamp_weight(view)
            out.update(action="enter", ticker=inst.symbol, weight=weight)
            out["results"] = run("BUY", inst, weight, view, skip_accounts=skip_enter)
    elif (
        st == "bear"
        and held
        and cfg.switch_instruments
        and (
            want := _switch_due(
                ctx.conn,
                market,
                today,
                cfg,
                held=held,
                want=port.instrument(market, leveraged=_leveraged(port, state, view)),
            )
        )
        is not None
    ):
        # 상품 교체: 오늘은 보유분 전량 매도만, 진입은 다음 운영일(bear 유지 시) 규칙이 want 를 산다
        out.update(action="switch", ticker=want.symbol)
        out["results"] = _sell_held(ctx, run, held, held_by, done_accounts, cfg, market)
        if not dry_run:
            control.set_flag(ctx.conn, switch_key(market), None)
            sold = ", ".join(sorted(held))
            _notify(
                ctx,
                messages.event(
                    "헤지 교체", f"{market} {sold} 매도 → 다음 운영일 {want.symbol} 진입"
                ),
            )
    elif st != "bear" and held:
        out["action"] = "exit"
        out["results"] = _sell_held(ctx, run, held, held_by, done_accounts, cfg, market)
    if not dry_run:
        results = out.get("results") or []
        failed = _failed_accounts(results) | unknown if out["action"] != "none" else set()
        touched = {str(r["account_id"]) for r in results if r.get("account_id")}
        flag = {
            "state": st,
            "action": out["action"],
            "done": sorted(done_accounts | (touched - failed)),
            "pending": sorted(failed),
            "retry": retry + 1 if failed else retry,
        }
        control.set_flag(ctx.conn, key, flag)
        if failed and flag["retry"] >= MAX_RETRIES:
            _notify(
                ctx,
                messages.event(
                    "헤지 보류",
                    f"{market} 계좌 {', '.join(sorted(failed))}",
                    f"{MAX_RETRIES}회 실패해 오늘은 더 시도하지 않아요.",
                ),
            )
        out["pending_accounts"] = sorted(failed)
    return out


def _sell_held(
    ctx: executor.ExecContext,
    run: _Runner,
    held: set[str],
    held_by: dict[str, set[str]],
    done_accounts: set[str],
    cfg: Any,
    market: str,
) -> list[dict]:
    """보유 헤지 종목마다 그 종목을 든 계좌(오늘 끝낸 계좌 제외)에서만 전량 SELL."""
    results: list[dict] = []
    for t in sorted(held):
        inst = _known(cfg, market, t) or _Inst(symbol=t)
        holders = {a for a, h in held_by.items() if t in h} - done_accounts
        skip_exit = frozenset({a.id for a in ctx.accounts} - holders)
        results += run("SELL", inst, None, None, skip_accounts=skip_exit)
    return results


def _risk_on_deferral(state: dict, view: dict | None, cfg: Any) -> str | None:
    """bear 진입 보류 사유(Phase 19). 조건: agent_signal ∧ 오늘 제안 RISK_ON ∧ confidence ≥ 기준 ∧
    정량 지표 수 == bear_min_signals(경계) ∧ 3/3 아님. 아니면 None."""
    if not cfg.agent_signal or not view:
        return None
    if view.get("stance") != "RISK_ON":
        return None
    conf = float(view.get("confidence") or 0)
    if conf < float(cfg.agent_min_confidence):
        return None
    sig = state.get("signals") or {}
    n = sum(int(bool(sig.get(k))) for k in ("ma_below", "momentum_weak", "vol_high"))
    if n >= 3 or n != cfg.bear_min_signals:
        return None
    return f"전략가 위험 선호(확신 {conf:.2f}) · 지표 {n}/3 경계 — 오늘은 진입을 보류해요"


@dataclass
class _Runner:
    """헤지 주문 1건(종목·방향): 시세 → 결정 행 → `executor.execute(is_hedge=True)` → 접수 알림."""

    ctx: executor.ExecContext
    market: str
    today: date
    cfg: Any
    state: dict
    quote_fn: Callable[[Any], Decimal]
    dry_run: bool

    def __call__(
        self,
        action: str,
        inst: Any,
        weight: float | None,
        view: dict | None,
        *,
        skip_accounts: frozenset[str] = frozenset(),
    ) -> list[dict]:
        ctx, market, state = self.ctx, self.market, self.state
        targets = [a.id for a in ctx.accounts if market in a.markets and a.id not in skip_accounts]
        try:
            price = Decimal(str(self.quote_fn(inst)))
        except (KisApiError, ValueError) as e:
            logger.warning("헤지 시세 실패 %s %s: %s", market, inst.symbol, e)
            _notify(ctx, messages.failure("헤지 보류", f"{market} {inst.symbol} 시세", e))
            return [{"error": f"시세 실패: {e}", "account_id": a} for a in targets] or [
                {"error": f"시세 실패: {e}"}
            ]
        if price <= 0:
            return [{"error": "시세 0"}]
        dec = hedge_decision(
            action,
            inst.symbol,
            market=market,
            weight=self.cfg.max_weight_pct if weight is None else weight,
            price=price,
            state=state,
            view=view,
        )
        did = spot_ledger.insert_hedge_decision(
            ctx.conn, self.today, market, inst.symbol, dec.model_dump()
        )
        results = executor.execute(
            ctx,
            dec,
            date=self.today,
            market=market,
            price=price,
            exchange=inst.exchange,
            dry_run=self.dry_run,
            decision_id=did,
            is_hedge=True,
            skip_accounts=skip_accounts,
        )
        label = labels.label(inst.symbol, labels.resolve(ctx.conn, market, inst.symbol))
        regime_name = messages.REGIME.get(str(state.get("state")), state.get("state"))
        for r in results:
            o = r.get("order")
            if o and o.get("accepted"):
                _notify(
                    ctx,
                    spot_messages.hedge(
                        entering=action == "BUY",
                        account=r["account_id"],
                        market=market,
                        label=label,
                        qty=r["qty"],
                        detail=(
                            f"국면 {regime_name} {state.get('count')}/3 · "
                            f"접수번호 {o.get('order_no')}"
                        ),
                    ),
                )
            # 승인 요청은 executor 가 '[승인 요청 #n · 실전 · 헤지]'(금액·만료 포함)로 이미 보낸다 —
            # 여기서 또 보내면 같은 요청이 두 번 온다(전체 검토 T1·T3)
        return results


def hedge_tick(deps: Any, market: str) -> dict:
    """데몬 monitor 틱의 헤지(옛 `_hedge_body`): 운영일 1회, bear 면 인버스 진입·벗어났으면 해제.
    regime 포트·계좌 포트·시세 클라이언트가 없거나 헤지가 꺼져 있으면 `{"skipped": "비활성"}`.
    오늘 완료 플래그가 있으면 `{"skipped": "오늘 이미 실행"}`, 일시정지면 하루 1회 알리고
    `{"skipped": "일시정지"}`. 시세는 KR `get_price_kr`, US `get_price_us(종목, 거래소 | AMS)`."""
    port = deps.ports.get("regime")
    accounts_port = deps.ports.get("accounts")
    if port is None or accounts_port is None or not port.hedge_cfg().enabled:
        return {"skipped": "비활성"}
    today = run_date_for(market, deps.now())
    conn = deps.conn_factory()
    try:
        if control.get_flag(conn, done_key(market, today)):
            return {"skipped": "오늘 이미 실행"}
        ectx = executor.exec_ctx_from(
            deps.settings, conn, accounts_port, deps.notifier or NullNotifier(), deps.now,
            deps.state, ports=deps.ports,
        )  # fmt: skip
        mc = ectx.market_client
        if mc is None:
            return {"skipped": "비활성"}
        if ectx.paused(conn):
            flag = f"hedge_paused_notice:{market}:{today.isoformat()}"
            if not control.get_flag(conn, flag):
                control.set_flag(conn, flag, True)
                _notify(
                    ectx,
                    messages.event(
                        "헤지 보류: 일시정지", f"{market} — 재개하면 다음 점검에서 실행해요"
                    ),
                )
            return {"skipped": "일시정지"}

        def quote(inst: Any) -> Decimal:
            q = (
                mc.get_price_us(inst.symbol, inst.exchange or "AMS")
                if market == "US"
                else mc.get_price_kr(inst.symbol)
            )
            return q.price

        return rebalance(
            ectx, market=market, today=today, quote_fn=quote, dry_run=bool(deps.dry_run)
        )
    finally:
        conn.close()


__all__ = [
    "HORIZON_DAYS",
    "MAX_RETRIES",
    "done_key",
    "hedge_decision",
    "hedge_tick",
    "rebalance",
    "switch_key",
]
