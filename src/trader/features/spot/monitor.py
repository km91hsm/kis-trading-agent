"""장중 모니터 (스펙 §5.2): 손절은 파이썬이 즉시 매도, 그 밖의 신호는 triggers 기록 → 재평가 세션.

- 손절선: 보유 기간 중 결정들의 손절 바닥(`spot.ledger.max_stop_since`)과 최신 결정 손절가 중 높은
  값, 둘 다 없으면 평단 × (1 + risk.stop_loss_pct) — 판정 규칙은 `monitor_rules`(순수 함수).
- 트리거: take_profit 도달, 프로필 임계 급등락, `invalidation_rules`, 단기 정기 재평가.
- 같은 종목 재평가는 프로필 간격(기본 30분)에 1회, 종류별 운영일 1회.
  재평가는 paused·세션 상한이면 기록만(손절은 paused 에서도 나간다).
- 데몬은 재평가를 큐(`enqueue_reevaluate`)로 넘기고, CLI 는 틱 안에서 인라인으로 돌린다.
- 주문은 `executor.execute(..., is_stop_loss=True)`, 취소는 `kernel.execution.core.cancel`
  만 지난다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from trader.features.spot import executor, fills, horizon, labels, resume
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.excluded import excluded_tickers
from trader.features.spot.monitor_rules import (
    Signal,
    check_position,
    closes_from_column,
    monotone_stop,
    needs_closes,
    periodic_signal,
)
from trader.features.spot.schemas import Decision
from trader.features.spot.settings import HorizonProfile
from trader.kernel.broker.errors import KisApiError
from trader.kernel.broker.models import Position
from trader.kernel.broker.ticks import quantize_price
from trader.kernel.config.time import KST, MARKET_TZ
from trader.kernel.execution import core
from trader.kernel.execution import ledger as xledger
from trader.kernel.execution.types import OrderBookkeepingError
from trader.kernel.lane.types import LaneContext
from trader.kernel.ledger import control
from trader.kernel.notify import messages
from trader.kernel.session import usage
from trader.kernel.session.calls import sessions_today

logger = logging.getLogger("trader.spot")

REEVAL_INTERVAL = timedelta(minutes=30)
STOP_LOSS_LIMIT_DISCOUNT = Decimal("0.03")  # US 손절은 현재가 3% 아래 지정가(체결 가능 지정가)
SL_REJECT_LIMIT = 3  # 손절이 이만큼 연속 거부되면 쉰다
SL_REJECT_COOLDOWN_MIN = 10
CLOSE_LOOKBACK = timedelta(days=45)  # `close` 무효화 조건용 종가표 창

CloseTable = Callable[[str, date, date], Mapping[str, Mapping[date, float | None]]]


@dataclass
class MonitorContext:
    """모니터 의존성. 설정·원장·계좌·브로커·알림·시각·모드 정책은 `exec`(executor.ExecContext).
    `enqueue_reevaluate(params) -> 요청 id | None` 이 있으면(데몬) 재평가를 큐로 넘기고, 없으면
    (CLI) `session_runner` 로 틱 안에서 돌린다. `close_table(시장, since, until)` 은 `close`
    무효화 조건용 완성 일봉 종가표(종목 → 날짜 → 종가)이고 없으면 그 조건은 미판정이다.
    `ports` 는 인라인 재평가 세션(LaneContext)에 넘긴다."""

    exec: executor.ExecContext
    market_client: Any
    session_runner: Callable[..., Any] | None = None
    enqueue_reevaluate: Callable[[dict], int | None] | None = None
    exchanges: dict[str, str] = field(default_factory=dict)  # US ticker → NAS|NYS|AMS
    close_table: CloseTable | None = None
    dry_run: bool = False
    ports: Mapping[str, Any] = field(default_factory=dict)


def _label(ctx: MonitorContext, market: str, ticker: str) -> str:
    return labels.label(ticker, labels.resolve(ctx.exec.conn, market, ticker))


def _exchange(ctx: MonitorContext, pos: Position) -> str | None:
    """US 거래소: 브로커 잔고가 준 값 우선, 없으면 ctx.exchanges. 둘 다 없으면 None (NAS 추정 금지:
    NYSE 종목을 NAS 로 조회하면 시세 0 → 손절 불능)."""
    return pos.exchange or ctx.exchanges.get(pos.ticker)


def _quote(ctx: MonitorContext, market: str, pos: Position) -> Decimal:
    if market == "KR":
        return ctx.market_client.get_price_kr(pos.ticker).price
    exchange = _exchange(ctx, pos)
    if exchange is None:
        raise ValueError(f"US 거래소 코드 없음 (잔고·universe 모두): {pos.ticker}")
    return ctx.market_client.get_price_us(pos.ticker, exchange).price


def _cached_quote(
    ctx: MonitorContext, market: str, pos: Position, quotes: dict[str, Decimal | None]
) -> Decimal | None:
    ticker = pos.ticker
    if ticker not in quotes:
        try:
            q: Decimal | None = _quote(ctx, market, pos)
        except (KisApiError, ValueError) as e:
            logger.warning("시세 실패 %s %s: %s", market, ticker, e)
            q = None
        if q is not None and q <= 0:
            logger.warning("시세 0 (거래소 코드·종목 확인): %s %s", market, ticker)
            q = None
        quotes[ticker] = q
    return quotes[ticker]


def _stop_loss_decision(ticker: str, market: str, quote: Decimal, sig: Signal) -> Decision:
    limit = quantize_price(market, quote * (1 - STOP_LOSS_LIMIT_DISCOUNT))
    return Decision(
        ticker=ticker,
        action="SELL",
        target_weight=0.0,
        order_type="MARKET" if market == "KR" else "LIMIT",
        limit_price=None if market == "KR" else float(limit),
        stop_loss=None,
        take_profit=None,
        horizon_days=1,
        confidence=1.0,
        rationale=(
            f"손절선 도달: 현재가 {quote} ≤ 손절선 {sig.detail.get('level')} (파이썬 즉시 매도)"
        ),
        invalidation_conditions=[],
    )


def _can_reevaluate(
    ctx: MonitorContext,
    market: str,
    ticker: str,
    today: date,
    interval: timedelta = REEVAL_INTERVAL,
) -> str | None:
    """재평가를 띄울 수 없으면 사유, 띄울 수 있으면 None."""
    ex = ctx.exec
    if ctx.session_runner is None:
        return "session_runner 없음"
    if ex.paused(ex.conn):
        return "일시정지"
    if spot_ledger.decision_id(ex.conn, today, market, ticker) is None:
        # submit-decision --stage 31_* 은 오늘 30_decision 을 요구한다. analyze 가 아직(또는 실패로)
        # 안 끝난 보유분은 세션을 띄워도 제출이 거부되므로 트리거만 남겨 다음 틱에 다시 본다
        return "오늘 결정 없음 (analyze 미완료)"
    if sessions_today(ex.conn, ex.now()) >= usage.session_cap(ex.conn, ex.settings)[0]:
        return "세션 상한"
    last = spot_ledger.last_trigger_handled_at(ex.conn, market, ticker)
    if last is not None and ex.now() - datetime.fromisoformat(last) < interval:
        return f"{int(interval.total_seconds() // 60)}분 내 재평가 있음"
    return None


# (종가표 콜러블 id, 시장, 운영일) → 종목 → 날짜 → 종가. 하루 한 번 읽는다
_CLOSES: dict[tuple[int, str, str], Mapping[str, Mapping[date, Any]]] = {}


def _closes_if_needed(
    ctx: Any, market: str, ticker: str, today: date, dec: dict | None
) -> list[Decimal | None] | None:
    """`close` 무효화 조건이 있는 결정만 완성 일봉 종가(오늘 전 봉)를 읽는다(L16). 종가 소스가
    없거나·읽기 실패·빈 표·종목 없음이면 None(그 조건만 미판정)."""
    if not needs_closes(dec):
        return None
    source = getattr(ctx, "close_table", None)
    if source is None:
        return None
    key = (id(source), market, today.isoformat())
    table = _CLOSES.get(key)
    if table is None:
        try:
            table = source(market, today - CLOSE_LOOKBACK, today - timedelta(days=1))
        except Exception as e:  # noqa: BLE001 - 종가가 없으면 그 조건만 미판정
            logger.warning("무효화 조건 종가 읽기 실패 %s: %s", market, e)
            return None
        _CLOSES.clear()
        _CLOSES[key] = table
    if not table or ticker not in table:
        return None
    # 거래일(표의 모든 날짜) 정렬을 지킨다 — 그 종목만 빠진 날·NaN 은 None 자리로 남긴다
    days = sorted({d for col in table.values() for d in col})
    column = table[ticker]
    return closes_from_column({d: column.get(d) for d in days})


def _floor_data(
    ctx: MonitorContext, account: Any, market: str, pos: Position, latest: dict | None
) -> dict | None:
    """보유 묶음 시작일 이후 날짜의 결정들의 손절 바닥으로 손절선을 단조 유지한다(전체 검토
    L5·2-O1). 기준은 결정 날짜다(결정 id 순서가 아니다 — 과거 asof 결정을 나중에 적재해도 현재
    보유분의 바닥을 올리지 않는다). 보유 기록이 없으면 바닥 없음.

    최신 결정이 보유 시작일 **이전** 날짜면 그 손절가는 지난 보유분의 것이다 — 버린다(청산 뒤
    결정 없이 다시 보유한 종목이 옛 손절가로 첫 틱에 팔리지 않게). 손절은 기본 손절률로 본다."""
    data = (latest or {}).get("data")
    since = xledger.held_since(ctx.exec.conn, account.id, [pos.ticker]).get(pos.ticker)
    if since is None:
        return data
    if data and latest and date.fromisoformat(str(latest["date"])) < since:
        data = {**data, "stop_loss": None}
    best = spot_ledger.max_stop_since(ctx.exec.conn, market, pos.ticker, None, since_date=since)
    return monotone_stop(data, best)


def monitor_once(ctx: MonitorContext, *, market: str, today: date) -> dict:
    """계좌별 보유종목 1회 점검. 1차: 손절(전 계좌), 2차: 트리거 기록·재평가.

    재평가 세션 실패는 트리거에 기록하고 다음 종목으로 넘어간다 (손절 점검을 막지 않는다). 한
    종목 점검의 예상 밖 예외도 `result["errors"]` 에 적고 다음 종목으로 넘어간다.
    `excluded_tickers`(헤지 ETF ∪ 현금 운용 ETF) 보유는 점검하지 않고 `skipped_excluded` 로 센다
    (하나라도 있을 때만 키가 생긴다).
    """
    ex = ctx.exec
    result: dict[str, Any] = {"checked": 0, "stop_loss_orders": 0, "triggers": 0, "reevaluated": 0}
    quotes: dict[str, Decimal | None] = {}
    pending: list[tuple[Any, Position, list[Signal], dict | None]] = []
    excluded = excluded_tickers(ex.settings, ctx.ports.get("regime"), market)
    scalp = ctx.ports.get("scalp")
    if scalp is not None:  # 단타 보유분은 단타 청산 규칙이 맡는다(현물 손절·트리거와 겹치지 않게)
        excluded = excluded | frozenset(scalp.open_tickers(ex.state, market, today))
    for account in ex.accounts:
        broker = ex.brokers.get(account.id)
        if market not in account.markets or broker is None:
            continue
        try:
            positions = broker.get_positions(market)
        except (KisApiError, ValueError) as e:
            logger.warning("보유 조회 실패 %s: %s", account.id, e)
            continue
        for pos in positions:
            if pos.ticker in excluded:
                # 헤지·현금 운용 ETF 는 국면 해제·현금 운용 규칙으로만 판다(손절·트리거 대상 아님)
                result["skipped_excluded"] = result.get("skipped_excluded", 0) + 1
                continue
            try:
                _check_one(ctx, account, market, pos, today, quotes, pending, result)
            except Exception as e:  # noqa: BLE001 - 한 종목 오류가 다른 종목 손절 점검을 막지 않는다
                logger.exception("종목 점검 실패 %s %s %s", account.id, market, pos.ticker)
                result.setdefault("errors", []).append(
                    f"{account.id} {market} {pos.ticker}: {type(e).__name__}: {e}"[:200]
                )
    _handle_triggers(ctx, market, today, pending, result)
    return result


def _check_one(
    ctx: MonitorContext,
    account: Any,
    market: str,
    pos: Position,
    today: date,
    quotes: dict[str, Decimal | None],
    pending: list[tuple[Any, Position, list[Signal], dict | None]],
    result: dict[str, Any],
) -> None:
    """보유 1종목: 시세 → 결정·손절 바닥 → 신호. 손절은 즉시 매도, 그 밖의 신호는 pending 에."""
    ex = ctx.exec
    quote = _cached_quote(ctx, market, pos, quotes)
    if quote is None:
        return
    result["checked"] += 1
    latest = spot_ledger.latest_decision(ex.conn, market, pos.ticker)
    dec_data = _floor_data(ctx, account, market, pos, latest)
    _, profile = horizon.of_decision(ex.settings.risk, dec_data)
    _warn_close_rule_unchecked(ctx, market, today, dec_data)
    signals = check_position(
        pos,
        dec_data,
        quote,
        stop_loss_pct=ex.settings.risk.stop_loss_pct,
        now=ex.now(),
        move_pct=Decimal(str(profile.move_pct)),
        closes=_closes_if_needed(ctx, market, pos.ticker, today, dec_data),
    )
    if not signals or signals[0].kind != "stop_loss":
        per = _periodic(ctx, market, pos, quote, profile, today)
        if per is not None:
            signals.append(per)
    if not signals:
        return
    if signals[0].kind == "stop_loss":
        result["stop_loss_orders"] += _sell_now(
            ctx, account, market, pos, quote, signals[0], today, latest
        )
    else:
        pending.append((account, pos, signals, latest))


def _warn_close_rule_unchecked(
    ctx: MonitorContext, market: str, today: date, dec: dict | None
) -> None:
    """Ruling 14 가시화: `close` 무효화 조건이 있는데 종가표 주입이 없으면 그 조건은 미판정이다 —
    시장·운영일당 경고 1회(control 플래그 `close_rule_unchecked:<시장>:<날짜>`)."""
    if ctx.close_table is not None or not needs_closes(dec):
        return
    key = f"close_rule_unchecked:{market}:{today.isoformat()}"
    if control.get_flag(ctx.exec.conn, key):
        return
    control.set_flag(ctx.exec.conn, key, True)
    logger.warning(
        "close 무효화 조건이 있지만 종가표(close_table) 주입이 없어 미판정 %s %s", market, today
    )


def _handle_triggers(
    ctx: MonitorContext,
    market: str,
    today: date,
    pending: list[tuple[Any, Position, list[Signal], dict | None]],
    result: dict[str, Any],
) -> None:
    """트리거 기록(같은 운영일·종류의 미처리 행 재사용) → 종목당 재평가 1회(큐 또는 인라인)."""
    ex = ctx.exec
    handled: set[str] = set()
    day_lo, day_hi = xledger.day_bounds(today, market)
    for account, pos, signals, latest in pending:
        tids: list[int] = []  # 이 종목의 트리거 전부 — 재평가 세션 1회가 모두 처리한다
        for sig in signals:
            # 같은 운영일에 같은 종류로 이미 재평가했으면 조건이 지속돼도 다시 쌓지 않는다
            # (익절선 위에 머무는 종목이 30분마다 유료 세션을 띄워 일일 상한을 소진하지 않도록)
            if spot_ledger.reevaluated_between(
                ex.conn, market, pos.ticker, sig.kind, day_lo, day_hi
            ):
                continue
            existing = spot_ledger.unhandled_trigger_id(
                ex.conn, market, pos.ticker, sig.kind, today
            )
            if existing is not None:  # 미처리 트리거가 있으면 다시 쌓지 않고 그 행을 쓴다
                tids.append(existing)
                continue
            tid = spot_ledger.insert_trigger(
                ex.conn,
                market=market,
                ticker=pos.ticker,
                kind=sig.kind,
                detail=sig.detail,
                price=sig.price,
                decision_id=(latest or {}).get("id"),
                account_id=account.id,
                created_at=ex.now().isoformat(),
            )
            tids.append(tid)
            result["triggers"] += 1
        if not tids or pos.ticker in handled:
            continue
        handled.add(pos.ticker)
        _, profile = horizon.of_decision(ex.settings.risk, (latest or {}).get("data"))
        why = _can_reevaluate(
            ctx, market, pos.ticker, today, timedelta(minutes=profile.reeval_interval_min)
        )
        if why:
            logger.info("재평가 생략 %s %s: %s", market, pos.ticker, why)
            continue
        if ctx.enqueue_reevaluate is not None:
            _enqueue(ctx, market, pos, tids, result)
            continue
        if _reevaluate_inline(ctx, market, today, pos, signals[0], tids):
            result["reevaluated"] += 1


def _enqueue(
    ctx: MonitorContext, market: str, pos: Position, tids: list[int], result: dict[str, Any]
) -> None:
    params = {
        "market": market,
        "ticker": pos.ticker,
        "exchange": _exchange(ctx, pos),
        "trigger_id": tids[0],
        "trigger_ids": tids[1:],
    }
    try:
        rid = ctx.enqueue_reevaluate(params)  # type: ignore[misc]
    except Exception as e:  # noqa: BLE001 - 큐 실패가 다른 종목 점검을 막지 않는다
        logger.warning("재평가 큐 등록 실패 %s %s: %s", market, pos.ticker, e)
        return
    if rid is not None:  # None = 같은 요청이 대기·실행 중(또는 kind 미등록) → 다음 틱
        logger.info("재평가 큐 등록 #%s %s %s", rid, market, pos.ticker)
        result["queued"] = result.get("queued", 0) + 1


def _reevaluate_inline(
    ctx: MonitorContext, market: str, today: date, pos: Position, s0: Signal, tids: list[int]
) -> bool:
    """CLI 경로: 재평가 세션을 틱 안에서 돌린다. 성공(ok)이면 True. 기동 실패는 트리거 전부를
    '실패:' 로 표시하고 알린다(같은 날 재시도 가능)."""
    ex = ctx.exec
    lane = LaneContext(
        settings=ex.settings,
        conn=ex.conn,
        state=ex.state,  # type: ignore[arg-type]
        now=ex.now,
        dry_run=ctx.dry_run,
        notifier=ex.notifier,
        ports=ctx.ports,
    )
    try:
        res = resume.reevaluate(
            lane,
            market=market,
            date=today,
            ticker=pos.ticker,
            exchange=_exchange(ctx, pos),
            trigger_id=tids[0],
            trigger={"kind": s0.kind, "price": str(s0.price), "detail": s0.detail},
            session_runner=ctx.session_runner,
        )
    except Exception as e:  # noqa: BLE001 - 세션 기동 실패가 다른 종목 점검을 막지 않는다
        logger.exception("재평가 세션 기동 실패 %s %s", market, pos.ticker)
        for tid in tids:
            spot_ledger.mark_trigger_handled(
                ex.conn,
                tid,
                f"실패: {type(e).__name__}: {e}"[:200],
                handled_at=ex.now().isoformat(),
            )
        core.notify_quietly(
            ex.notifier,
            messages.reevaluation_failed(
                market=market, label=_label(ctx, market, pos.ticker), err=e
            ),
        )
        return False
    for tid in tids[1:]:  # 같은 세션이 나머지 트리거도 처리한 것으로 표시 (실패도 같은 표시)
        spot_ledger.mark_trigger_handled(
            ex.conn, tid, resume.handled_marker(res), handled_at=ex.now().isoformat()
        )
    return bool(res.ok)


def _periodic(
    ctx: MonitorContext,
    market: str,
    pos: Position,
    quote: Decimal,
    profile: HorizonProfile,
    today: date,
) -> Signal | None:
    """정기 재평가 신호: 기준가는 오늘 마지막 트리거 가격(없으면 평단). 운영일 횟수·직전 시각은
    triggers 표의 kind='periodic' 행에서 센다."""
    if profile.periodic_check_min <= 0:
        return None
    conn = ctx.exec.conn
    lo, hi = xledger.day_bounds(today, market)
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(created_at) AS t FROM triggers WHERE market=? AND ticker=? "
        "AND kind='periodic' AND created_at >= ? AND created_at < ?",
        (market, pos.ticker, lo, hi),
    ).fetchone()
    last = datetime.fromisoformat(row["t"]) if row and row["t"] else None
    ref = spot_ledger.latest_trigger_price(conn, market, pos.ticker, today) or pos.avg_price
    return periodic_signal(
        quote,
        ref,
        profile=profile,
        last_periodic_at=last,
        count_today=int(row["n"] if row else 0),
        now=ctx.exec.now(),
    )


# --- 손절 집행 -------------------------------------------------------------------------


def _blocked_by_open_sell(
    ctx: MonitorContext, account: Any, market: str, ticker: str, local_day: date
) -> bool:
    """같은 현지 날짜의 살아 있는 SELL 이 있으면 True. 원장 status 는 fills sync(마감 뒤)가 바꾸므로
    장중엔 체결된 TRIM 도 accepted 로 남아 손절을 막는다 → 막힐 때만 그 계좌의 체결을 한 번 동기화해
    (KIS 1회) filled 로 바뀌면 막지 않는다."""
    ex = ctx.exec

    def open_sells() -> bool:
        return bool(
            xledger.open_orders_today(ex.conn, account.id, ticker, "SELL", local_day, market=market)
        )

    if not open_sells():
        return False
    broker = ex.brokers.get(account.id)
    if broker is not None:
        try:
            fills.sync_fills(ex.conn, broker, account, local_day, now=ex.now, notifier=ex.notifier)
        except Exception as e:  # noqa: BLE001 - 체결 조회 실패면 보수적으로 막힌 채로 둔다
            logger.warning("손절 전 체결 동기화 실패 %s %s: %s", account.id, ticker, e)
    return open_sells()


def _cancellable_resting_sell(row: Any, quote: Decimal) -> bool:
    """현재가보다 위에 걸린 지정가 SELL(아침 TRIM 등)만 손절 전 취소 대상이다. 시장가·현재가
    이하 지정가는 곧 체결될(또는 이미 낸 손절) 주문이라 취소하지 않고 그대로 둔다."""
    if row["order_type"] != "LIMIT" or row["price"] is None:
        return False
    return Decimal(str(row["price"])) > quote


def _cancel_rows(
    ctx: MonitorContext, account: Any, market: str, pos: Position, rows: list, reason: str
) -> tuple[int, int]:
    """주문 행들을 `executor.cancel_rows`(→ `core.cancel`)로 취소한다. (취소한 건수, 취소하지 못한
    건수)."""
    # 호출자(`_sell_now`)는 실제 손절을 낼 수 있을 때(`live`: dry-run·off·일시정지 아님)만 여기에
    # 온다(`_cancel_open_sells` 도 dry_run 이면 먼저 돌아간다) — 그래서 취소는 실제로 보낸다
    assert not ctx.dry_run, "dry-run 에서는 손절 전 취소를 보내지 않는다"
    exchange = _exchange(ctx, pos) if market == "US" else None
    done, left = executor.cancel_rows(
        ctx.exec, account.id, market, rows, exchange_of=lambda _t: exchange, reason=reason
    )
    return done, len(left)


def _cancel_open_sells(
    ctx: MonitorContext, account: Any, market: str, pos: Position, local_day: date, quote: Decimal
) -> bool:
    """손절 전 같은 종목의 당일 미체결 SELL 중 현재가 위 지정가를 전부 취소한다. 살아 있는
    SELL 이 하나도 남지 않으면 True(손절 진행), 하나라도 남으면 False(막힌 채 둔다)."""
    ex = ctx.exec
    rows = xledger.open_orders_today(
        ex.conn, account.id, pos.ticker, "SELL", local_day, market=market
    )
    if ex.brokers.get(account.id) is None or ctx.dry_run:
        return False
    if not all(_cancellable_resting_sell(r, quote) for r in rows):
        return False  # 시장가·현재가 이하 지정가(이미 낸 손절 포함)가 살아 있다 → 중복 방지
    _, left = _cancel_rows(ctx, account, market, pos, rows, "손절 전 미체결 SELL 취소")
    if left:
        core.notify_quietly(
            ex.notifier,
            messages.stop_deferred(
                account=account.id, market=market, label=_label(ctx, market, pos.ticker)
            ),
        )
    return not left


def _cancel_open_buys(
    ctx: MonitorContext, account: Any, market: str, pos: Position, local_day: date
) -> int:
    """손절할 종목의 당일 미체결 BUY(아침 ADD 지정가 등)를 취소한다 — 손절 뒤 체결돼 다시 보유하게
    되는 것을 막는다(전체 검토 O13). 실패는 경고만(손절은 그대로 진행). 취소한 건수."""
    assert not ctx.dry_run, "dry-run 에서는 손절 전 취소를 보내지 않는다"
    exchange = _exchange(ctx, pos) if market == "US" else None
    done, _left = executor.cancel_open_buys(
        ctx.exec,
        account.id,
        market,
        local_day,
        ticker=pos.ticker,
        exchange_of=lambda _t: exchange,
        reason="손절 전 미체결 BUY 취소",
    )
    return done


def _sell_now(
    ctx: MonitorContext,
    account: Any,
    market: str,
    pos: Position,
    quote: Decimal,
    sig: Signal,
    today: date,
    latest: dict | None,
) -> int:
    """손절 즉시 매도. 같은 날 같은 종목의 살아 있는 SELL 이 있으면 다시 내지 않는다.

    "같은 날" 은 시장 현지 날짜다 (US 는 KST 자정을 넘겨 장이 열려 있어 KST 날짜로 보면
    23:30 KST 에 낸 손절 매도를 00:30 KST 점검이 놓치고 다시 낸다)."""
    ex = ctx.exec
    local_day = ex.now().astimezone(MARKET_TZ.get(market, KST)).date()  # type: ignore[call-overload]
    mode, _ = ex.policy(ex.conn, account)
    paused = bool(ex.paused(ex.conn))
    # 실제로 손절 주문을 낼 수 있는 상태인가 — 아니면 KIS 쓰기(미체결 SELL 취소)를 하지 않는다.
    # off·dry-run 에서 취소만 하고 손절은 안 내면 보호 매도만 사라진다(전체 검토 O5)
    live = not ctx.dry_run and mode != "off"
    if live and _blocked_by_open_sell(ctx, account, market, pos.ticker, local_day):
        # 아침 TRIM 지정가 같은 미체결 SELL 이 하루 종일 손절을 막으면 안 된다 → 취소하고 진행.
        # 취소가 안 되면(응답 실패·이미 체결 중) 보수적으로 막힌 채 둔다
        if not _cancel_open_sells(ctx, account, market, pos, local_day, quote):
            return 0
    if live:
        _cancel_open_buys(ctx, account, market, pos, local_day)
        xledger.expire_pending_buys(
            ex.conn,
            account_id=account.id,
            market=market,
            ticker=pos.ticker,
            now=ex.now().isoformat(),
        )
    unrecorded = f"sl_unrecorded:{account.id}:{market}:{pos.ticker}:{local_day.isoformat()}"
    if control.get_flag(ex.conn, unrecorded):
        return 0  # 접수됐지만 원장에 못 적은 손절이 있다 (아래) — 오늘은 다시 내지 않는다
    # 기록만 하는 손절(dry-run·off)의 하루 1회 억제는 **그 상태에서만** — 같은 날 auto 로 바꾸면
    # 실제 손절이 나가야 한다(전체 검토 O15). 키에 실행 상태를 넣는다
    state = f"{'dry' if ctx.dry_run else 'live'}-{mode}"
    recorded = f"sl_recorded:{account.id}:{market}:{pos.ticker}:{local_day.isoformat()}:{state}"
    if control.get_flag(ex.conn, recorded):
        return 0  # dry-run·off 계좌의 손절은 기록만 하므로 하루 1회면 충분하다(매 분 반복 금지)
    rejects = f"sl_rejects:{account.id}:{market}:{pos.ticker}:{local_day.isoformat()}"
    held = control.get_flag(ex.conn, rejects) or {}
    if held.get("until") and ex.now() < datetime.fromisoformat(held["until"]):
        return 0  # 연속 거부로 쉬는 중 — 매 분 같은 주문·알림을 반복하지 않는다
    dec = _stop_loss_decision(pos.ticker, market, quote, sig)
    try:
        results = executor.execute(
            replace(ex, accounts=[account]),
            dec,
            date=today,
            market=market,
            price=quote,
            exchange=_exchange(ctx, pos),
            dry_run=ctx.dry_run,
            is_stop_loss=True,
            decision_id=(latest or {}).get("id"),
        )
    except OrderBookkeepingError as e:
        # 접수는 됐는데 orders 행이 없다: 다음 틱이 "살아 있는 SELL 없음" 으로 보고 같은 매도를
        # 다시 내지 않도록 플래그로 막고(알림은 코어가 보냈다) 다른 종목 점검은 계속한다
        logger.error("손절 주문 기록 실패 %s %s %s: %s", account.id, market, pos.ticker, e)
        control.set_flag(ex.conn, unrecorded, e.order_no or "-")
        return 0
    r = results[0] if results else {}
    if r and not r.get("error") and (ctx.dry_run or r.get("mode") == "off"):
        control.set_flag(ex.conn, recorded, True)
    order = r.get("order")
    if order and order.get("accepted"):
        core.notify_quietly(
            ex.notifier,
            messages.stop_loss(
                account=account.id,
                market=market,
                label=_label(ctx, market, pos.ticker),
                qty=r.get("qty"),
                quote=quote,
                level=sig.detail.get("level"),
                order_no=order.get("order_no"),
            ),
        )
        if paused:  # 위험 청산은 일시정지 중에도 나간다 — 실제 접수된 때만 하루 1회 알린다
            key = f"sl_paused_notified:{market}:{pos.ticker}:{today.isoformat()}"
            if not control.get_flag(ex.conn, key):
                control.set_flag(ex.conn, key, True)
                core.notify_quietly(
                    ex.notifier,
                    messages.stop_while_paused(
                        market=market,
                        label=_label(ctx, market, pos.ticker),
                        quote=quote,
                        level=sig.detail.get("level"),
                    ),
                )
        if held:
            control.set_flag(ex.conn, rejects, {})
        return 1
    if order and live and order.get("status") in ("rejected", "failed"):
        # 거부·실패는 실행 키가 풀려 다음 틱에 또 나간다 — 연속 SL_REJECT_LIMIT 번이면 쉰다
        count = int(held.get("count") or 0) + 1
        nxt: dict[str, Any] = {"count": count}
        if count % SL_REJECT_LIMIT == 0:
            nxt["until"] = (ex.now() + timedelta(minutes=SL_REJECT_COOLDOWN_MIN)).isoformat()
            core.notify_quietly(
                ex.notifier,
                messages.stop_retry_held(
                    account=account.id, market=market, label=_label(ctx, market, pos.ticker),
                    count=count, minutes=SL_REJECT_COOLDOWN_MIN,
                ),
            )  # fmt: skip
        control.set_flag(ex.conn, rejects, nxt)
    return 0
