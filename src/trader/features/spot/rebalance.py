"""주간 리밸런스 (Phase 25 단계 7). 최근 LLM 결정의 `target_weight` 대비 ±`rebalance.band_pct` 넘게
벗어난 보유만 되돌린다 — 새 판단은 하지 않는다(목표는 이미 트레이더가 정했다).

- 목표: 종목의 최근 결정(source≠hedge, action ∈ BUY·ADD·TRIM, `lookback_days` 안). HOLD·SELL·
  결정 없음은 건너뛴다(HOLD 의 target_weight 는 '그대로' 라 목표로 쓰면 안 된다).
- 제외: `excluded.excluded_tickers`(헤지 ETF·현금 운용 ETF). 단타(scalp) 보유는 M9 에서 연결한다
  (지금은 빈 집합).
- 순서: 모든 계좌의 TRIM 을 먼저, 그다음 ADD(현금을 먼저 만든다). 계좌별로 드리프트를 따로 재고
  밴드 안인 계좌는 `skip_accounts`.
- 주문: 파이썬이 만든 Decision(원 결정의 보유기간·익절·손절, 현재가 지정가) → `executor.execute(
  is_rebalance=True, decision_id=None)` → 체크포인트 `43_rebalance_*`·`53_order_rebalance_*`. 게이트
  규칙(최소 보유·주 2회·비중·섹터·테마·현금)은 그대로 적용된다. ADD 인데 원 손절선이 현재가 이상이면
  건너뛴다. 오늘 같은 방향 살아 있는 주문·대기 승인이 있는 계좌도 건너뛴다.
- 운영일 1회(`control.rebalance_done:<market>:<date>`). 일시정지면 플래그 없이 건너뛴다.
- 잡 `{market}_rebalance`(금요일 크론)·큐 kind `rebalance` 의 본문이 이 파일에 있다."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from trader.features.spot import executor
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.excluded import excluded_tickers
from trader.features.spot.schemas import Decision
from trader.features.spot.tradability import block_reason
from trader.kernel.execution import core
from trader.kernel.ledger import control
from trader.kernel.notify import messages
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.session.run_store import run_date_for

logger = logging.getLogger("trader.spot")

TARGET_ACTIONS = ("BUY", "ADD", "TRIM")


@dataclass(frozen=True)
class Leg:
    ticker: str
    current_weight: float
    target_weight: float
    drift: float  # 현재 − 목표
    action: Literal["TRIM", "ADD"]


def drift_legs(
    positions: dict[str, Any],
    nav: Decimal,
    targets: dict[str, float],
    *,
    band: float = 0.05,
    exclude: set[str] | frozenset[str] = frozenset(),
) -> list[Leg]:
    """|현재 비중 − 목표| > band 인 보유만. TRIM(드리프트 큰 순) 먼저, 그다음 ADD. 순수 함수."""
    if nav <= 0:
        return []
    trims, adds = [], []
    for t, p in positions.items():
        if t in exclude or t not in targets:
            continue
        cur = float(Decimal(str(p.eval_amount)) / nav)
        tgt = float(targets[t])
        d = cur - tgt
        if abs(d) <= band:
            continue
        leg = Leg(t, round(cur, 6), tgt, round(d, 6), "TRIM" if d > 0 else "ADD")
        (trims if d > 0 else adds).append(leg)
    trims.sort(key=lambda x: -x.drift)
    adds.sort(key=lambda x: x.drift)
    return trims + adds


def target_decision(conn, market: str, ticker: str, today: date, lookback_days: int) -> dict | None:
    """목표로 쓸 최근 결정 행(hydrate). 조건 밖이면 None."""
    d = spot_ledger.latest_decision(conn, market, ticker)
    if not d or d.get("source") == "hedge" or d.get("action") not in TARGET_ACTIONS:
        return None
    try:
        if date.fromisoformat(str(d["date"])) < today - timedelta(days=lookback_days):
            return None
    except (KeyError, ValueError):
        return None
    return d


def done_key(market: str, today: date) -> str:
    return f"rebalance_done:{market}:{today.isoformat()}"


def run(
    ctx: executor.ExecContext,
    *,
    market: str,
    today: date,
    dry_run: bool,
    quote_fn: Callable[[str], Any],
    exchange_of: Callable[[str], str | None] = lambda t: None,
) -> dict:
    """운영일 1회. 반환 {legs, orders, skipped?}. quote_fn(ticker) 는 현재가(Decimal) 또는
    `.price`·`.change_pct` 를 가진 시세 — 당일 등락률은 게이트의 급등 추격 금지로 넘긴다."""
    cfg = ctx.settings.rebalance
    if not cfg.enabled:
        return {"skipped": "rebalance.enabled=false"}
    if control.get_flag(ctx.conn, done_key(market, today)):
        return {"skipped": "오늘 이미 실행"}
    if ctx.paused(ctx.conn):
        # 판정·플래그 없이 건너뛴다 — 재개 뒤 그 주 리밸런스 유지(2-O15)
        return {"skipped": "일시정지"}
    # 단타(scalp) 보유 제외는 M9 에서 더한다
    exclude = set(excluded_tickers(ctx.settings, ctx.ports.get("regime"), market))
    accounts = [a for a in ctx.accounts if market in a.markets and a.id in ctx.brokers]
    need: dict[tuple[str, str], set[str]] = {}  # (action, ticker) → 계좌들
    legs_out: list[dict] = []
    sources: dict[str, dict] = {}
    for a in accounts:
        try:
            pf = executor.build_portfolio(
                ctx.conn, ctx.brokers[a.id], a, market, today,
                now=ctx.now(), record=ctx.record_portfolio,
            )  # fmt: skip
        except Exception as e:  # noqa: BLE001 - 계좌 하나의 잔고 실패는 그 계좌만 건너뛴다
            logger.warning("리밸런스 잔고 실패 %s: %s", a.id, e)
            continue
        positions = pf.equity_positions()
        targets: dict[str, float] = {}
        for t in positions:
            dec = sources.get(t) or target_decision(ctx.conn, market, t, today, cfg.lookback_days)
            if dec is not None:
                sources[t] = dec
                targets[t] = float(
                    dec.get("target_weight") or (dec.get("data") or {}).get("target_weight") or 0
                )
        for leg in drift_legs(positions, pf.nav, targets, band=cfg.band_pct, exclude=exclude):
            side = "SELL" if leg.action == "TRIM" else "BUY"
            if spot_ledger.has_open_order_today(
                ctx.conn, a.id, leg.ticker, side, today, market=market
            ) or spot_ledger.has_pending_approval(
                ctx.conn, a.id, leg.ticker, side, market=market, now=ctx.now().isoformat()
            ):
                legs_out.append(
                    {
                        "account_id": a.id,
                        **leg.__dict__,
                        "skipped": "오늘 같은 방향 주문·대기 승인 있음",
                    }
                )
                continue
            need.setdefault((leg.action, leg.ticker), set()).add(a.id)
            legs_out.append({"account_id": a.id, **leg.__dict__})
    orders: list[dict] = []
    all_ids = {a.id for a in ctx.accounts}
    for action in ("TRIM", "ADD"):
        for (act, ticker), accts in sorted(need.items()):
            if act != action:
                continue
            try:
                q = quote_fn(ticker)
                price = Decimal(str(getattr(q, "price", q)))
                change_pct = getattr(q, "change_pct", None)
                block = block_reason(q) if action == "ADD" else None
            except Exception as e:  # noqa: BLE001 - 시세 실패는 그 종목만 건너뛴다
                logger.warning("리밸런스 시세 실패 %s: %s", ticker, e)
                continue
            src = sources[ticker]
            data = src.get("data") or {}
            stop = data.get("stop_loss")
            if action == "ADD" and stop is not None and Decimal(str(stop)) >= price:
                orders.append({"ticker": ticker, "skipped": f"손절선 {stop} ≥ 현재가 {price}"})
                continue
            tgt = float(src.get("target_weight") or data.get("target_weight") or 0)
            dec = Decision(
                ticker=ticker,
                action=action,  # type: ignore[arg-type]
                target_weight=tgt,
                order_type="LIMIT",
                limit_price=float(price),
                stop_loss=stop,
                take_profit=data.get("take_profit"),
                horizon_days=int(data.get("horizon_days") or 5),
                confidence=float(
                    data["confidence"] if data.get("confidence") is not None else 0.6
                ),  # 0.0 도 원 결정 값 그대로(기본값은 값이 없을 때만)
                rationale=(
                    f"주간 리밸런스: 목표 비중 {tgt:.0%}(결정 #{src.get('id')} {src.get('date')})"
                    f"에서 {cfg.band_pct:.0%}p 넘게 벗어남"
                )[:500],
                invalidation_conditions=list(data.get("invalidation_conditions") or []),
            )
            res = executor.execute(
                ctx,
                dec,
                date=today,
                market=market,
                price=price,
                exchange=exchange_of(ticker),
                dry_run=dry_run,
                decision_id=None,
                skip_accounts=frozenset(all_ids - accts),
                is_rebalance=True,
                change_pct=change_pct,
                trade_block=block,
            )
            orders.append(
                {
                    "ticker": ticker,
                    "action": action,
                    "results": [
                        {k: r.get(k) for k in ("account_id", "approved", "qty", "reasons", "error")}
                        for r in res
                    ],
                }
            )
    if not dry_run:  # dry-run 은 실제 실행을 막지 않는다(같은 날 실제 리밸런스가 빠지던 버그)
        control.set_flag(ctx.conn, done_key(market, today), {"legs": len(legs_out)})
    return {"legs": legs_out, "orders": orders}


# --- 데몬 본문 (잡 `{market}_rebalance`·큐 kind `rebalance`) ----------------------------


def _rebalance_market(deps: Any, market: str) -> dict:
    """한 시장의 리밸런스(옛 `_rebalance_body`). 시세 클라이언트가 없으면 `{"skipped": …}`.
    게이트·계좌 모드·dry_run·paused 는 executor 가 그대로 본다."""
    accounts_port = deps.ports.get("accounts")
    if accounts_port is None:
        return {"error": "계좌 포트 없음 — 리밸런스 불가"}
    notifier = deps.notifier or NullNotifier()
    conn = deps.conn_factory()
    try:
        ectx = executor.exec_ctx_from(
            deps.settings, conn, accounts_port, notifier, deps.now, deps.state, ports=deps.ports,
        )  # fmt: skip
        mc = ectx.market_client
        if mc is None:
            return {"skipped": "시세 클라이언트 없음"}
        # US 는 유니버스 밖 보유도 있어 원장이 관측한 거래소로 시세·주문을 낸다
        exchanges = spot_ledger.ticker_exchanges(conn, market) if market == "US" else {}

        def exchange_of(t: str) -> str | None:
            return exchanges.get(t) if market == "US" else None

        def quote(t: str):
            if market == "KR":
                return mc.get_price_kr(t)  # price·change_pct(급등 추격 게이트)
            return mc.get_price_us(t, exchange_of(t) or "NAS")

        out = run(
            ectx,
            market=market,
            today=run_date_for(market, deps.now()),
            dry_run=bool(deps.dry_run),
            quote_fn=quote,
            exchange_of=exchange_of,
        )
    finally:
        conn.close()
    n = len(out.get("orders") or [])
    if n:
        core.notify_quietly(
            notifier,
            messages.event(
                "리밸런스", f"{market} 목표 비중 이탈 {len(out['legs'])}건 → 주문 시도 {n}건"
            ),
        )
    return out


def rebalance_body(deps: Any, job: Any) -> dict:
    """`{market}_rebalance` 크론 본문."""
    return _rebalance_market(deps, job.market or "KR")


def rebalance_request(deps: Any, params: dict, progress: Any) -> dict:
    """큐 `rebalance` 본문: 지정 시장(없으면 `deps.markets` 전체). 일시정지면 시장마다
    `{"skipped": "일시정지"}` 를 돌려주고 완료 플래그를 남기지 않는다."""
    market = params.get("market")
    return {m: _rebalance_market(deps, m) for m in ([market] if market else list(deps.markets))}


__all__ = [
    "TARGET_ACTIONS",
    "Leg",
    "done_key",
    "drift_legs",
    "rebalance_body",
    "rebalance_request",
    "run",
    "target_decision",
]
