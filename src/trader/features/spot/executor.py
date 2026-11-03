"""spot 계좌별 실행: 결정 1개 → 계좌마다 리스크 판정 → 멱등 claim → 승인 요청 또는 주문.

주문·승인 요청·승인 실행은 전부 커널 `execution.core` 를 지난다(`transmit`·`request_approval`·
`approve`). 이 모듈은 게이트(판정)·실행 키 선점·체크포인트·결과 조립만 맡는다. 계좌 모드·일시정지는
`ExecContext.policy`·`paused` 콜백(accounts 포트)으로 받는다 — 레인은 다른 기능을 import 하지
않는다. 국면·전략가 기울기·킬스위치·권리 일정은 `ExecContext.ports["regime"]`, 테마는
`ports["scan"]` 로 받고, 포트가 없으면 bull·기울기 0·킬스위치/블랙아웃 없음·테마 규칙 생략으로
본다. 알림·로그에는 계좌 id 만 쓴다.

메모리 서킷브레이커(M8)·RL 사이저(M7)·섹터 자동분류(M11)는 아직 없다(README 「알려진 차이」).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from trader.features.spot import labels, rules, stance
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.excluded import excluded_tickers
from trader.features.spot.schemas import Decision
from trader.kernel.broker.errors import KisApiError
from trader.kernel.config.time import KST
from trader.kernel.execution import core
from trader.kernel.execution import ledger as xledger
from trader.kernel.execution.core import KeepPending, approval_fingerprint
from trader.kernel.execution.idem import ExecKeyStore
from trader.kernel.execution.types import (
    ExecPolicy,
    GatePlan,
    OrderBookkeepingError,
    OrderIntent,
)
from trader.kernel.notify import messages
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.registry.types import OriginSpec
from trader.kernel.session.run_store import (
    checkpoint_payload,
    run_date_for,
    run_dir,
    write_checkpoint,
)

logger = logging.getLogger("trader.spot")

__all__ = [
    "ORIGINS",
    "ExecContext",
    "approval_fingerprint",
    "approve",
    "build_portfolio",
    "cancel_open_buys",
    "cancel_rows",
    "deps_of",
    "exec_ctx",
    "exec_ctx_from",
    "execute",
    "expire_approvals",
    "origin_of",
    "plan_of",
    "reject",
]

# 실행 종류 표: (origin, 판정 단계, 주문 단계, 결정 행 있음, 게이트 면제, 멱등 단위).
# 재평가(resume)는 llm 행을 쓰되 단계에 `_r<n>` 을, 단타는 `_<leg>` 를 붙인다(`origin_of`).
ORIGINS = (
    OriginSpec("llm", "40_risk_check", "50_order", True, frozenset(), "decision"),
    OriginSpec(
        "stop", "45_stop_loss", "55_order_sl", True,
        frozenset({"min_hold", "weekly_cap", "decision_idem"}), "stage",
    ),
    OriginSpec(
        "hedge", "46_hedge", "56_order_hedge", True,
        frozenset({"min_hold", "weekly_cap", "regime"}), "decision",
    ),
    OriginSpec(
        "scalp", "47_scalp", "57_order_scalp", False,
        frozenset({"min_hold", "weekly_cap", "expected_return"}), "stage",
    ),
    OriginSpec("rebalance", "43_rebalance", "53_order_rebalance", False, frozenset(), "stage"),
    OriginSpec("reprice", "42_reprice", "52_order_reprice", True, frozenset(), "decision"),
    OriginSpec(
        "park", "48_park", "58_order_park", False,
        frozenset({"min_hold", "weekly_cap", "regime"}), "stage",
    ),
)  # fmt: skip
_BY_ORIGIN = {o.origin: o for o in ORIGINS}


def origin_of(
    *,
    is_stop_loss: bool = False,
    scalp_leg: str | None = None,
    is_reprice: bool = False,
    is_rebalance: bool = False,
    is_hedge: bool = False,
    is_park: bool = False,
    resume_round: int | None = None,
) -> OriginSpec:
    """실행 종류 플래그 → OriginSpec(체크포인트 단계는 실제 이름). 우선순위 stop > scalp >
    reprice > rebalance > hedge > park > resume > llm — 손절·재평가가 오늘 분석의 40_/50_ 을
    덮지 않게 한다."""
    if is_stop_loss:
        return _BY_ORIGIN["stop"]
    if scalp_leg is not None:
        o = _BY_ORIGIN["scalp"]
        return replace(
            o, risk_stage=f"{o.risk_stage}_{scalp_leg}", order_stage=f"{o.order_stage}_{scalp_leg}"
        )
    for flag, name in (
        (is_reprice, "reprice"),
        (is_rebalance, "rebalance"),
        (is_hedge, "hedge"),
        (is_park, "park"),
    ):
        if flag:
            return _BY_ORIGIN[name]
    llm = _BY_ORIGIN["llm"]
    if resume_round is not None:
        return replace(
            llm,
            risk_stage=f"41_risk_check_r{resume_round}",
            order_stage=f"51_order_r{resume_round}",
        )
    return llm


@dataclass
class ExecContext:
    """실행 의존성. `policy(conn, account) -> (모드, 사유)`·`paused(conn) -> bool` 은 accounts
    포트가 준다. `holiday_fn(시장, 날짜)` 는 보유 거래일 계산용(없으면 평일 수).
    `record_portfolio=False` 면 판정만 하고 positions_snapshot 을 쓰지 않는다(FAKE 세션).
    `ports` 는 `Boot.ports`(이름 → 구현체) — 게이트는 `regime`·`scan` 만 본다."""

    settings: Any
    accounts: list
    conn: sqlite3.Connection
    brokers: dict[str, Any]
    policy: Callable[[Any, Any], tuple[str, str | None]]
    paused: Callable[[Any], bool]
    notifier: Any = field(default_factory=NullNotifier)
    state: Path | None = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    holiday_fn: Callable[[str, date], bool] | None = None
    record_portfolio: bool = True
    market_client: Any | None = None
    ports: Mapping[str, Any] = field(default_factory=dict)


def exec_ctx_from(
    settings: Any,
    conn: sqlite3.Connection,
    accounts_port: Any | None,
    notifier: Any,
    now: Callable[[], datetime] | None,
    state: Path | None,
    *,
    record_portfolio: bool = True,
    ports: Mapping[str, Any] | None = None,
) -> ExecContext:
    """ExecContext 단일 조립 경로(CLI·텔레그램 공용). 계좌 포트가 없으면 계좌 없음(fail-closed:
    정책 없음 → 모든 계좌 off). `ports` 는 게이트가 볼 regime·scan 포트(없으면 빈 dict)."""
    accounts: list = []
    brokers: dict[str, Any] = {}
    market_client = holiday_fn = None
    policy: Callable[[Any, Any], tuple[str, str | None]] | None = None
    paused: Callable[[Any], bool] | None = None
    if accounts_port is not None:
        accounts = list(accounts_port.accounts())
        raw = accounts_port.brokers()
        brokers = dict(raw) if isinstance(raw, dict) else {b.account.id: b for b in raw}
        market_client = accounts_port.market_client()
        holiday_fn = accounts_port.holiday_fn()
        policy, paused = accounts_port.policy, accounts_port.paused
    policy, paused = policy_pair(policy, paused)
    kw: dict[str, Any] = {} if now is None else {"now": now}
    return ExecContext(
        settings=settings,
        accounts=accounts,
        conn=conn,
        brokers=brokers,
        policy=policy,
        paused=paused,
        notifier=notifier if notifier is not None else NullNotifier(),
        state=state,
        holiday_fn=holiday_fn,
        record_portfolio=record_portfolio,
        market_client=market_client,
        ports=dict(ports or {}),
        **kw,
    )


def boot_notifier(boot: Any) -> Any:
    """부트 결과로 알림기를 만든다(spot CLI·도구·분석 공용). settings `notify.enabled` 를 따르고,
    시크릿이 없거나 조립이 실패하면(OSError·ValueError) 경고 후 NullNotifier — 알림 때문에
    실행을 막지 않는다."""
    from trader.kernel.notify import make_notifier

    if boot.secrets is None:
        return NullNotifier()
    enabled = boot.settings.notify.enabled if boot.settings is not None else True
    try:
        return make_notifier(boot.secrets, enabled=enabled)
    except (OSError, ValueError) as e:
        logger.warning("알림 조립 실패, 알림 없이 진행: %s", type(e).__name__)
        return NullNotifier()


def exec_ctx(boot: Any, *, record_portfolio: bool = True) -> ExecContext:
    """부트 결과(`ports=True`)로 ExecContext 를 조립한다. 계좌 포트가 없으면 계좌 없음."""
    from trader.kernel.platform import paths

    return exec_ctx_from(
        boot.settings,
        boot.conn,
        boot.ports.get("accounts"),
        boot_notifier(boot),
        None,
        paths.state_dir(),
        record_portfolio=record_portfolio,
        ports=boot.ports,
    )


def _no_policy(conn: Any, account: Any) -> tuple[str, str | None]:
    return "off", "계좌 정책 포트 없음: 주문 없음"


def _not_paused(conn: Any) -> bool:
    return False


def _always_paused(conn: Any) -> bool:
    return True


def policy_pair(
    policy: Callable[[Any, Any], tuple[str, str | None]] | None,
    paused: Callable[[Any], bool] | None,
) -> tuple[Callable[[Any, Any], tuple[str, str | None]], Callable[[Any], bool]]:
    """주입된 (정책, 일시정지) 콜백의 빈 자리를 안전하게 채운다(fail-closed).
    정책이 없으면 모든 계좌 off(주문 없음). 정책은 있는데 일시정지 콜백이 없으면 일시정지로 본다 —
    paused 를 놓쳐 신규 주문이 나가는 일이 없게(경고 로그)."""
    if policy is None:
        return _no_policy, paused or _not_paused
    if paused is None:
        logger.warning("일시정지 콜백 없이 계좌 정책만 주입됨: 일시정지로 취급(주문 없음)")
        return policy, _always_paused
    return policy, paused


def deps_of(ctx: ExecContext) -> core.ExecDeps:
    """코어 의존성(실행 키 저장소·브로커 조회·종목 표기)."""
    return core.ExecDeps(
        conn=ctx.conn,
        notifier=ctx.notifier,
        now=ctx.now,
        idem=ExecKeyStore(ctx.conn),
        broker_of=ctx.brokers.get,
        label=labels.label,
    )


def _shown(ctx: ExecContext, market: str, ticker: str) -> str:
    """알림용 '이름(코드)' 표기 (원장만, 네트워크 없음)."""
    return labels.label(ticker, labels.resolve(ctx.conn, market, ticker))


# --- 미체결 매수 취소 ------------------------------------------------------------------


def cancel_rows(
    ctx: ExecContext,
    account_id: str,
    market: str,
    rows: list,
    *,
    exchange_of: Callable[[str], str | None] = lambda _t: None,
    reason: str,
) -> tuple[int, list[str]]:
    """주문 행들을 `core.cancel` 로 취소한다(브로커 취소의 유일한 경로). (취소 건수, 취소하지 못한
    종목들). 브로커 없음·응답 없는(번호 없는) 주문·US 거래소를 모르는 주문·KIS 거부·이미 끝난
    주문·예외(`KisApiError`·`ValueError`)는 '못 함'. 위험을 줄이는 취소라 실제로 보낸다 — dry-run
    호출자는 여기 오기 전에 돌아간다."""
    broker = ctx.brokers.get(account_id)
    deps = deps_of(ctx)
    done, left = 0, []
    for r in rows:
        t = str(r["ticker"])
        ex = exchange_of(t) if market == "US" else None
        if broker is None or not r["order_no"] or (market == "US" and not ex):
            left.append(t)
            continue
        try:
            res = core.cancel(
                deps,
                order_id=int(r["id"]),
                order_no=str(r["order_no"]),
                market=market,
                broker=broker,
                dry_run=False,
                reason=reason,
                exchange=ex,
            )
        except (KisApiError, ValueError) as e:
            logger.warning("%s 실패 %s %s #%s: %s", reason, account_id, t, r["order_no"], e)
            left.append(t)
            continue
        if res.get("ok"):
            done += 1
        else:
            left.append(t)
    return done, left


def cancel_open_buys(
    ctx: ExecContext,
    account_id: str,
    market: str,
    day: date,
    *,
    ticker: str | None = None,
    exchange_of: Callable[[str], str | None] = lambda _t: None,
    keep: frozenset[str] | None = None,
    side: str = "BUY",
    reason: str | None = None,
) -> tuple[int, list[str]]:
    """당일(시장 현지) 미체결 주문(기본 BUY)을 `cancel_rows` 로 취소한다 — 결정이 매수에서
    돌아섰을 때·손절 직전 등. (취소 건수, 취소하지 못한 종목들) — 호출자가 알린다. 브로커가 없으면
    (0, [])."""
    if ctx.brokers.get(account_id) is None:
        return 0, []
    keep = keep or frozenset()
    rows = [
        r
        for r in xledger.open_orders_today(ctx.conn, account_id, ticker, side, day, market=market)
        if str(r["ticker"]) not in keep
    ]
    return cancel_rows(
        ctx, account_id, market, rows, exchange_of=exchange_of,
        reason=reason or f"미체결 {side} 취소",
    )  # fmt: skip


# --- 포트폴리오 ------------------------------------------------------------------------


def _holidays_between(
    fn: Callable[[str, date], bool] | None, market: str, start: date | None, end: date
) -> set[date]:
    if fn is None or start is None or start >= end:
        return set()
    out: set[date] = set()
    d = start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            try:
                if fn(market, d):
                    out.add(d)
            except Exception:  # noqa: BLE001 - 휴장일 조회 실패는 개장일로 본다
                pass
    return out


def _balance(broker: Any, market: str) -> tuple[list, Any]:
    get_balance = getattr(broker, "get_balance", None)
    if get_balance is not None:  # KR 은 잔고 API 1회로 보유·예수금을 함께
        return get_balance(market)
    return broker.get_positions(market), broker.get_cash(market)


def build_portfolio(
    conn: sqlite3.Connection,
    broker: Any,
    account: Any,
    market: str,
    today: date,
    *,
    now: datetime | None = None,
    record: bool = True,
    hedge_tickers: frozenset[str] = frozenset(),
    theme_of: Callable[[str], list[str] | None] | None = None,
    cancel_buys: bool = False,
    killswitch_pct: float = 0.0,
    regime: Any | None = None,
    cancel_fn: Callable[..., tuple[int, list[str]]] | None = None,
) -> rules.Portfolio:
    """브로커 잔고·현금 + 원장 집계로 Portfolio 를 만들고(`record` 면) positions_snapshot 을 남긴다.
    now 가 있으면 만료된 승인 요청은 현금 예약에서 뺀다. 계좌 `capital_cap` 이 있으면 게이트가
    쓰는 NAV·가능금액을 그 금액까지로 자른다 — 스냅샷·킬스위치는 실제 NAV 그대로.

    `regime`(RegimePort) 이 있고 `killswitch_pct > 0`·`record` 면 실제 NAV 로 누적 손실 킬스위치를
    본다(FAKE 세션은 관측하지 않는다). `cancel_buys`(주문을 낼 수 있는 호출만) 이고 킬스위치가 이
    계좌에 켜져 있으면(자동·수동 어느 경로든) `cancel_fn(account_id, market, today, keep=…)` 로
    당일 미체결 BUY 를 거둔다 — 게이트는 새 주문만 막는다. `hedge_tickers`(헤지·현금 운용 ETF)는
    거두지 않고 종목 수·섹터 계산에서도 뺀다(옛 `on_killswitch`)."""
    pos_list, cash = _balance(broker, market)
    positions = {p.ticker: p for p in pos_list}
    for p in positions.values():  # 잔고 응답의 종목명·US 거래소는 추가 호출 없이 얻는 관측값
        spot_ledger.upsert_ticker_name(
            conn, p.market, p.ticker, getattr(p, "name", None), "balance"
        )
        spot_ledger.upsert_ticker_exchange(conn, p.market, p.ticker, getattr(p, "exchange", None))
    nav = spot_ledger.nav_of(pos_list, cash)
    if regime is not None and killswitch_pct > 0 and record:
        regime.killswitch_observe(
            conn,
            account_id=account.id,
            market=market,
            nav=nav,
            today=today,
            limit_pct=killswitch_pct,
            positions=[(p.ticker, p.qty, p.current_price) for p in pos_list],
        )
    if (
        regime is not None
        and cancel_buys
        and cancel_fn is not None
        and regime.killswitch_reason(conn, account.id)
    ):
        n, left = cancel_fn(account.id, market, today, keep=hedge_tickers)
        if n or left:
            logger.warning(
                "킬스위치: %s %s 당일 미체결 BUY 취소 %d건, 못 함 %s", account.id, market, n, left
            )
    prev = xledger.latest_nav_before(conn, account.id, today, market=market)
    today_pnl = float(nav / prev - 1) if prev else 0.0
    if record:
        xledger.snapshot_positions(
            conn,
            account_id=account.id,
            d=today,
            market=market,
            nav=nav,
            cash=cash.total,
            positions=[
                {
                    "ticker": p.ticker,
                    "market": p.market,
                    "qty": p.qty,
                    "avg_price": p.avg_price,
                    "current_price": p.current_price,
                    "eval_amount": p.eval_amount,
                }
                for p in positions.values()
            ],
        )
    sizing_nav, available = nav, cash.available
    cap = (getattr(account, "capital_cap", None) or {}).get(market)
    if cap is not None and nav > cap:
        # 투자금은 보유 평가액 합계(미결제 예수금이 섞인 NAV − 현금으로 보면 결제 전 매수가 빠진다)
        invested = sum((p.eval_amount for p in pos_list), Decimal(0))
        sizing_nav = Decimal(cap)
        available = max(Decimal(0), min(cash.available, Decimal(cap) - invested))
    marks = xledger.ledger_marks(conn)  # 아래 원장 집계가 본 주문·승인의 경계
    at = now.isoformat() if now is not None else None
    return rules.Portfolio(
        account_id=account.id,
        market=market,
        nav=sizing_nav,
        cash_available=available,
        positions=positions,
        today_pnl_pct=today_pnl,
        today_buy_tickers=xledger.today_buy_tickers(conn, account.id, today, market=market),
        # 가능금액은 체결된 매수를 이미 반영하므로 미체결 live 주문만 더 뺀다
        today_order_amount=xledger.today_order_amount(
            conn, account.id, today, market=market, exclude_filled=True, now=at
        ),
        pending_buys=xledger.pending_buy_amounts(conn, account.id, today, market=market, now=at),
        held_since=xledger.held_since(conn, account.id, tickers=list(positions)),
        trades_this_week=xledger.trades_this_week(conn, account.id, today, market=market),
        hedge_tickers=hedge_tickers,
        theme_of=theme_of,
        ledger_marks=marks,
    )


def _with_reservations(pf: rules.Portfolio, inflight: list[dict]) -> rules.Portfolio:
    """진행 중 매수 예약을 체결 전 매수처럼 더한 예상 포트폴리오 — 현금 차감·종목 수·당일 중복
    매수가 그 예약을 본다."""
    extra = Decimal(0)
    pending = dict(pf.pending_buys)
    tickers = set(pf.today_buy_tickers)
    for r in inflight:
        try:
            amt = Decimal(str(r.get("amount") or 0))
        except ArithmeticError:
            amt = Decimal(0)
        t = str(r.get("ticker") or "")
        extra += amt
        if t:
            pending[t] = pending.get(t, Decimal(0)) + amt
            tickers.add(t)
    return replace(
        pf,
        today_order_amount=pf.today_order_amount + extra,
        pending_buys=pending,
        today_buy_tickers=tickers,
    )


def _exec_key(
    decision_id: int | None,
    account_id: str,
    side: str | None,
    order_stage: str,
    ticker: str,
    d: date,
    *,
    is_stop_loss: bool = False,
    qty: int = 0,
    tag: str | None = None,
) -> str:
    """실행 키. 결정 주문은 `결정|계좌|방향|주문단계`, 손절은 `stop|계좌|종목|현지일|q수량`(결정과
    무관하게 하루 단위), 결정 행이 없는 주문(단타·리밸런스·현금 운용)은 `주문단계|계좌|종목|방향|
    운영일[|tag]`, 단타 청산은 끝에 `|q수량`. 수량 꼬리는 일부 체결 뒤 남은 수량을 같은 날 다시 낼
    수 있게 한다. `tag` 는 결정 행 없는 주문의 세대 꼬리표."""
    if is_stop_loss:
        return f"stop|{account_id}|{ticker}|{d.isoformat()}|q{qty}"
    if decision_id is None:
        key = f"{order_stage}|{account_id}|{ticker}|{side}|{d.isoformat()}"
        if tag:
            key += f"|{tag}"
        return key + f"|q{qty}" if order_stage.startswith("57_order_scalp_exit") else key
    return f"{decision_id}|{account_id}|{side}|{order_stage}"


# --- 게이트 → 계획 ----------------------------------------------------------------------


def plan_of(
    verdict: rules.RiskVerdict,
    origin: OriginSpec,
    account: Any,
    *,
    decision: Decision,
    market: str,
    exchange: str | None,
    price: Decimal,
    decision_id: int | None,
    idem_key: str | None,
    name: str | None = None,
) -> GatePlan:
    """판정 → `GatePlan`. 승인되고 수량이 있는 판정만 주문 의도 1건(`OrderIntent`)을 싣는다.
    시세가(`price`)는 시장가 금액 계산용 `quote_price` 로 남긴다. 손절·헤지·단타·현금 운용은
    파이썬 결정이라 알림 의견(`opinion`)을 붙이지 않는다."""
    intents: list[OrderIntent] = []
    if verdict.approved and int(verdict.adjusted_qty or 0) > 0 and verdict.side:
        python_made = origin.origin in ("stop", "hedge", "scalp", "park")
        intents.append(
            OrderIntent(
                account_id=account.id,
                ticker=decision.ticker,
                market=market,
                exchange=exchange,
                side=verdict.side,
                qty=int(verdict.adjusted_qty),
                order_type=str(verdict.order_type),
                price=verdict.price,
                origin=origin.origin,
                risk_stage=origin.risk_stage,
                order_stage=origin.order_stage,
                idem_key=idem_key or "",
                decision_id=decision_id,
                quote_price=price,
                name=name,
                opinion=None if python_made else decision.model_dump(mode="json"),
            )
        )
    return GatePlan(
        approved=verdict.approved,
        reasons=list(verdict.reasons),
        intents=intents,
        verdict_payload=verdict.model_dump(mode="json"),
    )


def _flat_order(out: dict) -> dict:
    """코어 전송 결과 → 옛 결과 모양(브로커 결과 + status·order_id)."""
    return {
        **(out.get("result") or {}),
        "status": out.get("status"),
        "order_id": out.get("order_id"),
    }


# --- 국면·테마 게이트 입력(포트) -----------------------------------------------------------


@dataclass(frozen=True)
class _Gates:
    """시장 단위 게이트 입력. 포트가 없으면 기본값(bull·기울기 0·제외 종목 없음·테마 규칙 생략)."""

    regime: str = "bull"
    agent_tilt: int = 0
    excluded: frozenset[str] = frozenset()
    theme_of: Callable[[str], list[str] | None] | None = None
    hedge_max_weight: float | None = None  # 헤지 주문의 비중 상한(`hedge.max_weight_pct`)


def _agent_tilt(port: Any, conn: sqlite3.Connection, market: str, today: date) -> int:
    """오늘 시장 세션 제안의 기울기(+1 RISK_ON / −1 RISK_OFF). 헤지가 꺼져 있거나 `agent_gate` 가
    꺼져 있거나 확신이 `agent_min_confidence` 미만이면 0(기존 국면 한도 그대로)."""
    cfg = port.hedge_cfg()
    if not cfg.enabled or not cfg.agent_gate:
        return 0
    view = port.view_today(conn, market, today)
    return stance.tilt_of(view, min_confidence=cfg.agent_min_confidence)


def _theme_of(
    ctx: ExecContext, market: str, today: date
) -> Callable[[str], list[str] | None] | None:
    """`ports["scan"]` 의 종목→오늘 상위 테마. 포트가 없거나 조회가 실패하면 None(테마 규칙만
    빠진다)."""
    scan = ctx.ports.get("scan")
    if scan is None:
        return None
    try:
        return scan.theme_of(ctx.conn, market, today)
    except Exception as e:  # noqa: BLE001 - 테마 조회 실패가 주문 판정을 막지 않는다
        logger.warning("테마 조회 실패, 테마 규칙 생략 %s: %s", market, e)
        return None


def _gates(ctx: ExecContext, market: str, today: date) -> _Gates:
    port = ctx.ports.get("regime")
    excluded = excluded_tickers(ctx.settings, port, market)
    theme_of = _theme_of(ctx, market, today)
    if port is None:
        return _Gates(excluded=excluded, theme_of=theme_of)
    return _Gates(
        regime=port.state(ctx.conn, market),
        agent_tilt=_agent_tilt(port, ctx.conn, market, today),
        excluded=excluded,
        theme_of=theme_of,
        hedge_max_weight=port.hedge_cfg().max_weight_pct,
    )


# --- 실행 ------------------------------------------------------------------------------


def execute(
    ctx: ExecContext,
    decision: Decision,
    *,
    date: date,
    market: str,
    price: Decimal,
    exchange: str | None = None,
    dry_run: bool = True,
    is_stop_loss: bool = False,
    decision_id: int | None = None,
    resume_round: int | None = None,
    is_hedge: bool = False,
    skip_accounts: frozenset[str] | None = None,
    extra_note: str | None = None,
    scalp_leg: str | None = None,
    is_reprice: bool = False,
    is_rebalance: bool = False,
    change_pct: float | None = None,
    max_qty: dict[str, int] | None = None,
    trade_block: str | None = None,
    is_park: bool = False,
    exec_tag: str | None = None,
    park_max_weight: float | None = None,
) -> list[dict]:
    """계좌마다 판정·기록·(모드별) 승인 요청/주문. 계좌 하나의 실패가 다른 계좌를 막지 않는다.

    실행 종류(손절·단타·재호가·리밸런스·헤지·현금 운용·재평가)는 `origin_of` 표 하나로
    (판정·주문 단계, origin, 결정 행 유무)를 정한다. extra_note 는 판정 note 에 덧붙이는 문구
    (재시세 등), change_pct 는 판정 시점 당일 등락률(급등 추격 금지), trade_block 은 매매 제한 종목
    사유(BUY 거부 — KR 일반 BUY/ADD 는 `ports["regime"]` 의 권리 일정 블랙아웃 사유를 합친다),
    max_qty 는 계좌별 수량 상한(줄이기만 한다), skip_accounts 는 건너뛸 계좌. 국면·전략가 기울기·
    헤지 비중 상한·제외 종목·테마는 `_gates`(포트) 가 시장 단위로 한 번 정한다.
    is_stop_loss 면 보유기간·주 2회 규칙을 면제하고 approve 계좌도 승인 없이 즉시 낸다.

    접수 뒤 기록 실패(`OrderBookkeepingError`)는 다른 계좌를 마친 뒤 다시 올리고, 그 밖의 예외는
    그 계좌의 `error` 항목 + 실행 실패 알림으로 남긴다. LLM 결정이 BUY/ADD 가 아니고 dry_run 이
    아니면 그 종목의 당일 미체결 BUY 를 거둔다(일시정지와 무관 — 위험을 줄이는 취소)."""
    skip = skip_accounts or frozenset()
    d = run_dir(date, market, decision.ticker, ctx.state)
    origin = origin_of(
        is_stop_loss=is_stop_loss,
        scalp_leg=scalp_leg,
        is_reprice=is_reprice,
        is_rebalance=is_rebalance,
        is_hedge=is_hedge,
        is_park=is_park,
        resume_round=resume_round,
    )
    # 결정 행이 없는 실행(단타·리밸런스·현금 운용)은 오늘 LLM 결정 행에 묶지 않는다 — 그 결정의
    # 멱등 조건이 이 주문을 막거나 반대로 섞인다
    if decision_id is None and origin.has_decision_row:
        decision_id = spot_ledger.decision_id(ctx.conn, date, market, decision.ticker)
    port = ctx.ports.get("regime")
    if (
        port is not None
        and market == "KR"
        and decision.action in ("BUY", "ADD")
        and not is_hedge
        and not is_park
    ):
        # 권리 일정 블랙아웃(기본 끔): 모든 매수 경로(분석·리밸런스·단타)가 지난다
        ev = port.blackout_reason(ctx.conn, decision.ticker, date)
        if ev:
            trade_block = f"{trade_block} · {ev}" if trade_block else ev
    gates = _gates(ctx, market, date)
    if origin.origin == "park" and park_max_weight is not None:
        hedge_weight: float | None = park_max_weight
    elif origin.origin in ("hedge", "park"):
        hedge_weight = gates.hedge_max_weight
    else:
        hedge_weight = None
    deps = deps_of(ctx)
    results: list[dict] = []
    bookkeeping_error: OrderBookkeepingError | None = None
    for account in ctx.accounts:
        if market not in account.markets or account.id in skip:
            continue
        try:
            results.append(
                _execute_account(
                    ctx,
                    deps,
                    account,
                    decision,
                    d=d,
                    date=date,
                    market=market,
                    price=price,
                    exchange=exchange,
                    dry_run=dry_run,
                    origin=origin,
                    decision_id=decision_id,
                    extra_note=extra_note,
                    change_pct=change_pct,
                    max_qty=None if max_qty is None else max_qty.get(account.id),
                    trade_block=trade_block,
                    hedge_weight=hedge_weight,
                    exec_tag=exec_tag,
                    gates=gates,
                )
            )
        except OrderBookkeepingError as e:  # 접수 뒤 기록 실패: 다른 계좌를 마친 뒤 올린다
            results.append({"account_id": account.id, "error": str(e), "order_no": e.order_no})
            bookkeeping_error = bookkeeping_error or e
        except Exception as e:  # noqa: BLE001 - 원장 잠금·OSError 등: 다음 계좌는 계속 간다
            logger.exception("계좌 %s 실행 실패 %s %s", account.id, market, decision.ticker)
            core.notify_quietly(
                ctx.notifier,
                messages.execution_failure(
                    account=account.id,
                    market=market,
                    label=_shown(ctx, market, decision.ticker),
                    err=e,
                ),
            )
            results.append({"account_id": account.id, "error": f"{type(e).__name__}: {e}"})
    if origin.origin == "llm" and decision.action not in ("BUY", "ADD") and not dry_run:
        # 결정이 매수에서 돌아섰다(재평가 SELL·HOLD 등) — 이미 낸 당일 지정가 BUY 가 그 뒤에
        # 체결되면 매도로 돌아선 종목을 사게 된다
        for account in ctx.accounts:
            if market not in account.markets or account.id in skip:
                continue
            n, left = cancel_open_buys(
                ctx,
                account.id,
                market,
                date,
                ticker=decision.ticker,
                exchange_of=lambda _t: exchange,
            )
            if n or left:
                core.notify_quietly(
                    ctx.notifier,
                    messages.open_buys_cancelled(
                        account=account.id,
                        label=_shown(ctx, market, decision.ticker),
                        reason=f"결정 {decision.action}",
                        cancelled=n,
                        left=left,
                    ),
                )
    if bookkeeping_error is not None:
        # 호출자(submit-decision 의 CRITICAL 등)가 접수번호로 수동 대조한다
        raise bookkeeping_error
    return results


def _reject(verdict: rules.RiskVerdict, *reasons: str) -> rules.RiskVerdict:
    return verdict.model_copy(
        update={"approved": False, "adjusted_qty": 0, "reasons": verdict.reasons + list(reasons)}
    )


def _execute_account(
    ctx: ExecContext,
    deps: core.ExecDeps,
    account: Any,
    decision: Decision,
    *,
    d: Path,
    date: date,
    market: str,
    price: Decimal,
    exchange: str | None,
    dry_run: bool,
    origin: OriginSpec,
    decision_id: int | None,
    extra_note: str | None,
    change_pct: float | None,
    max_qty: int | None,
    trade_block: str | None,
    hedge_weight: float | None,
    exec_tag: str | None,
    gates: _Gates,
) -> dict:
    """계좌 하나: 정책 → 성향 → 포트폴리오(킬스위치 관측·미체결 BUY 회수) → 게이트 → 결정 단위
    멱등 → 수량 상한 → 실행 키 → 판정 기록 → 계획 → 승인 요청/주문(코어). 반환은 results 항목
    하나."""
    is_stop_loss = origin.origin == "stop"
    is_hedge = origin.origin in ("hedge", "park")
    mode, note = ctx.policy(ctx.conn, account)
    policy = ExecPolicy(mode=mode, note=note, paused=bool(ctx.paused(ctx.conn)), dry_run=dry_run)
    broker = ctx.brokers.get(account.id)
    st = stance.decide(xledger.realized_pnl_5d(ctx.conn, account.id, date, market=market))
    portfolio: rules.Portfolio | None = None
    gate: Callable[[rules.Portfolio], rules.RiskVerdict] | None = None
    port = ctx.ports.get("regime")

    def cancel(
        account_id: str, market: str, day: date, *, keep: frozenset[str]
    ) -> tuple[int, list[str]]:
        return cancel_open_buys(
            ctx,
            account_id,
            market,
            day,
            exchange_of=lambda t: spot_ledger.exchange_of(ctx.conn, market, t),
            keep=keep,
            reason="킬스위치 미체결 BUY 취소",
        )

    try:
        if broker is None:
            raise KisApiError("NO_BROKER", f"계좌 {account.id} 브로커 없음(앱키 미설정)")
        portfolio = build_portfolio(
            ctx.conn,
            broker,
            account,
            market,
            date,
            now=ctx.now(),
            record=ctx.record_portfolio,
            hedge_tickers=gates.excluded,
            theme_of=gates.theme_of,
            cancel_buys=not dry_run,
            killswitch_pct=ctx.settings.risk.killswitch_drawdown_pct,
            regime=port,
            cancel_fn=cancel,
        )
        # 재판정(gate)은 쓰기 트랜잭션 안에서 돈다 — 휴장일 조회·킬스위치는 여기서 한 번만
        hol = _holidays_between(
            ctx.holiday_fn, market, portfolio.held_since.get(decision.ticker), date
        )
        kill = port.killswitch_reason(ctx.conn, account.id) if port is not None else None

        def gate(pf: rules.Portfolio) -> rules.RiskVerdict:
            return rules.evaluate(
                decision,
                pf,
                limits=ctx.settings.risk,
                stance=st,
                market=market,
                price=price,
                today=date,
                is_stop_loss=is_stop_loss,
                holidays=hol,
                regime=gates.regime,
                is_hedge=is_hedge,
                hedge_max_weight=hedge_weight,
                agent_tilt=gates.agent_tilt,
                is_scalp=origin.origin == "scalp",
                change_pct=change_pct,
                killswitch=kill,
                trade_block=trade_block,
            )

        verdict = gate(portfolio)
    except (KisApiError, ValueError) as e:
        verdict = rules.RiskVerdict(
            account_id=account.id,
            approved=False,
            adjusted_qty=0,
            reasons=[f"잔고 조회 실패로 판정 불가: {e}"],
        )
    # 결정 단위 멱등: 같은 결정·계좌에 살아 있는 주문·승인 요청이 있으면 다시 내지 않는다.
    # 손절은 보지 않는다(며칠 전 결정의 미체결 손절 SELL 이 다음 손절을 영구히 막는다)
    if (
        verdict.approved
        and decision_id is not None
        and not is_stop_loss
        and xledger.has_open_order_or_approval(
            ctx.conn,
            decision_id,
            account.id,
            verdict.side,
            include_filled=not origin.order_stage.startswith("51_"),  # 재평가 라운드
            now=ctx.now().isoformat(),
        )
    ):
        verdict = _reject(verdict, "이미 이 결정에 대한 주문/승인 요청이 존재")
    if max_qty is not None and verdict.approved and verdict.adjusted_qty > max_qty:
        capped = max(int(max_qty), 0)
        verdict = verdict.model_copy(
            update={
                "approved": capped > 0,
                "adjusted_qty": capped,
                "reasons": verdict.reasons + [f"호출자 수량 상한 {capped}주"],
            }
        )
    # 실행 키 선점: 위의 중복 확인과 주문 사이에서 다른 프로세스가 같은 주문·승인 요청을 동시에
    # 내지 않게, 주문(또는 승인 요청)이 나갈 판정이면 키를 먼저 잡는다
    key: str | None = None
    if verdict.approved and policy.transmits:
        key = _exec_key(
            decision_id,
            account.id,
            verdict.side,
            origin.order_stage,
            decision.ticker,
            date,
            is_stop_loss=is_stop_loss,
            qty=int(verdict.adjusted_qty or 0),
            tag=exec_tag,
        )
        claimed, verdict, race = _claim(
            deps, key, verdict, portfolio, gate, account_id=account.id, market=market,
            ticker=decision.ticker, price=price, now=ctx.now(),
        )  # fmt: skip
        if not claimed:
            key = None
            verdict = _reject(
                verdict,
                *(race or ["다른 프로세스가 같은 주문을 실행 중이거나 이미 냄(실행 키 선점됨)"]),
            )
    if policy.paused and verdict.approved:
        note = (note + "; " if note else "") + "일시정지 중: 판정만 기록, 주문·승인 요청 없음"
    if gates.regime != "bull" and not is_hedge:
        note = (note + "; " if note else "") + f"국면 {gates.regime}"
    if origin.origin == "scalp":
        note = (note + "; " if note else "") + "분 단위 단타(모의 전용)"
    if extra_note:
        note = (note + "; " if note else "") + extra_note
    verdict = verdict.model_copy(
        update={"mode": mode, "note": note, "stance": st, "dry_run": dry_run}
    )
    entry: dict[str, Any] = {
        "account_id": account.id,
        "mode": mode,
        "note": note,
        "stance": st,
        "approved": verdict.approved,
        "qty": verdict.adjusted_qty,
        "reasons": verdict.reasons,
        "dry_run": dry_run,
        "order": None,
        "approval_id": None,
    }
    try:
        plan = plan_of(
            verdict,
            origin,
            account,
            decision=decision,
            market=market,
            exchange=exchange,
            price=price,
            decision_id=decision_id,
            idem_key=key,
            name=labels.resolve(ctx.conn, market, decision.ticker),
        )
        # 주문 요청 검증(pydantic) — 키를 잡은 채 코어 안에서 실패하면 키가 미발송으로 남는다
        for intent in plan.intents:
            intent.request()
        write_checkpoint(
            d, f"{origin.risk_stage}_{account.id}", checkpoint_payload(plan.verdict_payload)
        )
        if decision_id is not None:
            xledger.insert_risk_check(
                ctx.conn,
                decision_id=decision_id,
                account_id=account.id,
                approved=verdict.approved,
                adjusted_qty=verdict.adjusted_qty,
                reasons=verdict.reasons,
            )
    except BaseException:
        if key is not None:  # 주문 전 실패: 키를 놓아 다음 실행이 다시 낼 수 있게
            deps.idem.release(key)
        raise
    if not plan.intents or not policy.transmits:
        if key is not None:  # 판정은 통과했지만 낼 주문이 없다(0주 등)
            deps.idem.release(key)
        return entry
    intent = plan.intents[0]
    if policy.mode == "approve" and not is_stop_loss:
        try:
            entry["approval_id"] = core.request_approval(
                deps, intent, env=account.env, reasons=verdict.reasons
            )
        except BaseException:
            # 승인에 묶인 키를 풀면 다른 경로가 같은 키를 잡아 같은 주문이 두 번 나갈 수 있다
            if key is not None and not deps.idem.has_approval(key):
                deps.idem.release(key)
            raise
    else:
        out = core.transmit(deps, intent, run_dir=d, mode=policy.mode, broker=broker)
        entry["order"] = _flat_order(out)
    return entry


def _claim(
    deps: core.ExecDeps,
    key: str,
    verdict: rules.RiskVerdict,
    portfolio: rules.Portfolio | None,
    gate: Callable[[rules.Portfolio], rules.RiskVerdict] | None,
    *,
    account_id: str,
    market: str,
    ticker: str,
    price: Decimal,
    now: datetime,
) -> tuple[bool, rules.RiskVerdict, list[str]]:
    """실행 키를 잡는다. 매수는 다른 경로의 진행 중 예약을 반영해 게이트를 다시 돌린 뒤에만 키와
    예약(금액)을 한 쓰기 트랜잭션에서 함께 쓴다(줄이거나 거부). 매도는 키만.
    (잡았나, 최종 판정, 거부 사유)."""
    race: list[str] = []
    if verdict.side != "BUY" or portfolio is None or gate is None:
        return deps.idem.claim(key, now=now.isoformat()), verdict, race
    first = verdict
    final = verdict

    def recheck(inflight: list[dict]) -> tuple[bool, Decimal | None]:
        nonlocal final
        v = first
        if inflight:
            v2 = gate(_with_reservations(portfolio, inflight))
            if not v2.approved or v2.adjusted_qty <= 0:
                race.append(
                    f"동시 판정: 진행 중 매수 예약 {len(inflight)}건 반영 시 거부 — "
                    + "; ".join(v2.reasons)
                )
                return False, None
            if v2.adjusted_qty < v.adjusted_qty:
                v = v.model_copy(
                    update={
                        "adjusted_qty": v2.adjusted_qty,
                        "reasons": v.reasons
                        + [
                            f"동시 판정: 진행 중 매수 예약 {len(inflight)}건 반영으로 "
                            f"수량 {v.adjusted_qty}→{v2.adjusted_qty}"
                        ],
                    }
                )
        final = v
        px = v.price if v.price is not None else price
        return True, Decimal(int(v.adjusted_qty)) * Decimal(str(px))

    claimed, _seen = deps.idem.claim_reserved(
        key,
        now=now.isoformat(),
        account_id=account_id,
        market=market,
        ticker=ticker,
        side="BUY",
        marks=portfolio.ledger_marks,
        recheck=recheck,
    )
    return claimed, final, race


# --- 승인 처리 (approve 모드) ------------------------------------------------------------


def _account(ctx: ExecContext, account_id: str) -> Any | None:
    return next((a for a in ctx.accounts if a.id == account_id), None)


def _decision_row(ctx: ExecContext, decision_id: int | None) -> dict | None:
    if not decision_id:
        return None
    return spot_ledger.get_decision(ctx.conn, decision_id, hydrate=True)


def _approval_recheck(ctx: ExecContext) -> Callable[[dict, dict], str | KeepPending | None]:
    """승인 실행 직전 최소 재판정(BUY 만, 헤지 제외). 요청 뒤 킬스위치가 켜졌거나(신규 매수 중단을
    승인으로 우회하지 않게), bear 로 바뀌었거나(전략가 RISK_ON 기울기 없이), 일일 손실 한도에
    닿았으면 사유(→ 만료). 킬스위치·국면은 `ports["regime"]` 가 있을 때만 본다.
    잔고를 못 읽으면 `KeepPending("balance")` — 판정할 수 없으니 실행하지도 만료시키지도 않는다.
    수량은 다시 계산하지 않는다."""

    def recheck(a: dict, order: dict) -> str | KeepPending | None:
        if order.get("side") != "BUY" or order.get("origin") == "hedge":
            return None
        dec = _decision_row(ctx, a.get("decision_id"))
        if dec is not None and dec.get("source") == "hedge":
            return None
        market = str(order.get("market"))
        broker = ctx.brokers.get(a["account_id"])
        today = run_date_for(market, ctx.now())
        port = ctx.ports.get("regime")
        if port is not None:
            ks = port.killswitch_reason(ctx.conn, a["account_id"])
            if ks:
                return f"킬스위치(신규 매수 중단): {ks}"
            if (
                port.state(ctx.conn, market) == "bear"
                and _agent_tilt(port, ctx.conn, market, today) <= 0
            ):
                return "국면 bear: 신규 매수 중단"
        try:
            pos_list, cash = _balance(broker, market)
        except (KisApiError, ValueError, AttributeError) as e:
            logger.warning("승인 재판정 잔고 조회 실패 #%s %s: %s", a["id"], a["account_id"], e)
            return KeepPending("balance")
        prev = xledger.latest_nav_before(ctx.conn, a["account_id"], today, market=market)
        if prev:
            pnl = float(spot_ledger.nav_of(pos_list, cash) / prev - 1)
            stop = ctx.settings.risk.daily_loss_stop_pct
            if pnl <= stop:
                return f"일일 손실 {pnl:.2%} 이 한도 {stop:.0%} 초과 — 신규 매수 금지"
        return None

    return recheck


def _decision_side_of(ctx: ExecContext) -> Callable[[int], str | None]:
    """승인 뒤 결정이 바뀌었는지 볼 결정 방향. 헤지 결정은 보지 않고(None), HOLD 처럼 방향이 없는
    결정은 action 그대로 돌려 주문 방향과 달라 만료되게 한다(관망으로 돌아선 매수를 내지 않게)."""

    def side_of(decision_id: int) -> str | None:
        dec = _decision_row(ctx, decision_id)
        if dec is None or dec.get("source") == "hedge":
            return None
        return spot_ledger.decision_side(ctx.conn, decision_id) or str(dec.get("action") or "")

    return side_of


def approve(ctx: ExecContext, approval_id: int) -> dict:
    """pending·미만료 승인 요청을 주문으로 실행한다(`core.approve`). 두 번째 호출은 거부된다.
    반환 {ok, approval_id, status, error?, order?} — order 는 옛 모양(브로커 결과 + status·
    order_id).
    접수 뒤 기록 실패는 승인을 failed 로 두고 접수번호를 error 에 담는다."""
    deps = deps_of(ctx)

    def policy_of(account_id: str) -> ExecPolicy | None:
        account = _account(ctx, account_id)
        if account is None:
            return None
        mode, note = ctx.policy(ctx.conn, account)
        return ExecPolicy(mode=mode, note=note, paused=bool(ctx.paused(ctx.conn)), dry_run=False)

    def run_dir_of(a: dict) -> Path:
        o = a["order"]
        dec = _decision_row(ctx, a.get("decision_id"))
        day = date.fromisoformat(dec["date"]) if dec else ctx.now().astimezone(KST).date()
        return run_dir(day, str(o["market"]), str(o["ticker"]), ctx.state)

    def opinion_of(a: dict) -> dict | None:
        dec = _decision_row(ctx, a.get("decision_id"))
        return (dec or {}).get("data")

    try:
        r = core.approve(
            deps,
            approval_id,
            policy_of=policy_of,
            run_dir_of=run_dir_of,
            decision_side_of=_decision_side_of(ctx),
            recheck=_approval_recheck(ctx),
            opinion_of=opinion_of,
        )
    except OrderBookkeepingError as e:  # 코어가 승인을 failed 로 바꾼 뒤 올린다
        return {
            "ok": False,
            "approval_id": approval_id,
            "status": "failed",
            "error": f"주문 기록 실패(접수번호 {e.order_no or '-'}): {e.__cause__}",
        }
    if isinstance(r.get("order"), dict):
        r["order"] = _flat_order(r["order"])
    return r


def reject(ctx: ExecContext, approval_id: int) -> dict:
    """pending·미만료 승인 요청을 거부하고 알린다(`core.reject`)."""
    return core.reject(deps_of(ctx), approval_id)


def expire_approvals(ctx: ExecContext) -> list[int]:
    """만료 시각이 지난 pending 승인을 expired 로 바꾸고 id 목록을 돌려준다."""
    return core.expire_approvals(deps_of(ctx))
