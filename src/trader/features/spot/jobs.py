"""spot 데몬 잡: `approvals_expire`(만료 승인 정리)·큐 `approve_execute`(결정된 승인 집행,
취소하면 선점 해제)·`approval_notice`(웹 거부 알림)·
`fills_sync`(체결 동기화)·`balance_refresh`·`{market}_reconcile`(야간 대사)·
`{market}_rebalance`(주간 리밸런스)·`{market}_monitor`(장중 감시)·`notice_listener`(체결통보)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from trader.features.spot import (
    approvals,
    balance,
    cash_park,
    executor,
    fills,
    hedge,
    labels,
    locks,
    monitor,
    notice,
    rebalance,
    reconcile,
    reprice,
    resume,
)
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.messages import reject_notice
from trader.features.spot.scan_analyze import QUEUE_KINDS_SCAN
from trader.kernel.broker import factory
from trader.kernel.config.market_hours import REGULAR, session_over
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.queue_jobs import enqueue_from_job
from trader.kernel.execution import core
from trader.kernel.execution import ledger as xledger
from trader.kernel.lane.types import LaneContext
from trader.kernel.ledger import control
from trader.kernel.notify import messages
from trader.kernel.notify.telegram import NullNotifier, send
from trader.kernel.registry.types import JobSpec, QueueKind
from trader.kernel.session import calls
from trader.kernel.session.run_store import run_date_for
from trader.kernel.session.runner import run_session

logger = logging.getLogger("trader.spot")


def expire_body(ctx, job) -> dict:
    """boot 없이 데몬 의존성으로 돈다 — 만료 처리는 계좌·브로커가 필요 없다(원장 + 알림만)."""
    conn = ctx.conn_factory()
    try:
        ectx = executor.ExecContext(
            settings=ctx.settings,
            accounts=[],
            conn=conn,
            brokers={},
            policy=executor._no_policy,
            paused=executor._not_paused,
            notifier=ctx.notifier or NullNotifier(),
            state=ctx.state,
            now=ctx.now,
        )
        return {"expired": executor.expire_approvals(ectx)}
    finally:
        conn.close()


APPROVALS_EXPIRE = JobSpec(
    id="approvals_expire", kind="approvals", trigger="interval", per_market=False,
    schedule_key=None, interval_s=60, enabled=None, gate="none", heavy=False,
    misfire="idempotent", catch_up="none", start_soon_s=30, body=expire_body,
)  # fmt: skip


def sync_all(ctx) -> dict:
    """계좌마다 시장별 현지 오늘 체결을 적재하고, 아직 정리되지 않은 지난 날짜 주문이 있으면 그
    날짜도 다시 조회한다. 조회에 실패한 계좌·시장이 있으면 최상위 `error` 로 올려 runs·하트비트·
    운영 점검에 오류로 보이게 한다. 한 계좌의 실패가 다른 계좌 동기화를 막지 않는다."""
    if ctx.ports.get("accounts") is None:
        return {"error": "계좌 포트 없음 — 체결 동기화 불가"}
    out: dict[str, Any] = {}
    failures: list[str] = []
    conn = ctx.conn_factory()
    try:
        notifier = ctx.notifier or NullNotifier()
        ectx = executor.exec_ctx_from(
            ctx.settings, conn, ctx.ports.get("accounts"), notifier, ctx.now, ctx.state
        )
        for a in ectx.accounts:
            b = ectx.brokers.get(a.id)
            if b is None or getattr(a, "asset", "stock") != "stock":
                continue
            try:
                # US 체결은 ET 날짜로 조회해야 한다 (07:10 KST 는 전날 ET) → 시장별 현지 날짜
                r = fills.sync_fills(conn, b, a, ctx.now(), now=ctx.now, notifier=notifier)
                out[a.id] = r
                failures += [f"{a.id} {m}" for m in r.get("failed_markets") or []]
            except Exception as e:  # noqa: BLE001 - 한 계좌의 실패가 다른 계좌를 막지 않는다
                logger.warning("체결 동기화 실패 %s: %s", a.id, e)
                out[a.id] = {"error": f"{type(e).__name__}: {e}"}
                failures.append(f"{a.id} ({type(e).__name__})")
                continue
            for m in a.markets:
                one = a.model_copy(update={"markets": [m]})
                for day in fills.stale_open_days(conn, a.id, m, run_date_for(m, ctx.now())):
                    try:
                        past = fills.sync_fills(conn, b, one, day, now=ctx.now, notifier=notifier)
                    except Exception as e:  # noqa: BLE001
                        failures.append(f"{a.id} {m} {day} ({type(e).__name__})")
                        continue
                    out.setdefault(f"{a.id}:lookback", []).append(past)
                    failures += [f"{a.id} {x} {day}" for x in past.get("failed_markets") or []]
    finally:
        conn.close()
    if failures:
        out["error"] = "체결 조회 실패: " + ", ".join(failures)
    return out


def fills_body(ctx, job) -> dict:
    """`fills_sync` 크론 본문."""
    return sync_all(ctx)


def balance_refresh(ctx, *, force: bool = False) -> dict:
    """시장별 잔고 갱신(조회만). 정규장 시장은 매 틱, 장 밖 시장은 스냅샷이 `OFF_HOURS_EVERY`
    보다 오래됐을 때만(입출금·이체 반영). force 면 장 밖이어도 바로 읽는다. `ports["regime"]` 가
    있으면 킬스위치를 관측하고, dry-run 이 아니면 장중 시장에서 킬스위치 계좌의 당일 미체결 BUY 를
    거둔다(장 밖엔 거둘 당일 주문이 없다 — 옛 `cancel_buys=not dry_run and m in open_now`)."""
    stop = getattr(ctx, "stop_event", None)
    if stop is not None and stop.is_set():  # 일시정지는 거래 중단일 뿐 — 잔고 조회는 계속
        return {"skipped": "종료 중"}
    if ctx.ports.get("accounts") is None:
        return {"error": "계좌 포트 없음 — 잔고 갱신 불가"}
    now = ctx.now()
    markets = list(ctx.markets)
    open_now = set(balance.open_markets(markets, now))
    conn = ctx.conn_factory()
    try:
        ectx = executor.exec_ctx_from(
            ctx.settings, conn, ctx.ports.get("accounts"), ctx.notifier, ctx.now, ctx.state,
            ports=ctx.ports,
        )  # fmt: skip
        dry_run = bool(getattr(ctx, "dry_run", True))  # 모르면 취소하지 않는다
        return {
            m: balance.refresh(
                conn,
                accounts=ectx.accounts,
                brokers=ectx.brokers,
                market=m,
                settings=ctx.settings,
                now=now,
                state=ctx.state,
                only_stale=not force and m not in open_now,
                stale_after=balance.OFF_HOURS_EVERY,
                regime=ctx.ports.get("regime"),
                cancel_buys=not dry_run and m in open_now,
                cancel_fn=executor.killswitch_canceller(ectx),
                notifier=ectx.notifier,
                scan=ctx.ports.get("scan"),
            )
            for m in markets
        }
    finally:
        conn.close()


def balance_body(ctx, job) -> dict:
    """`balance_refresh` interval 잡 본문."""
    return balance_refresh(ctx)


def fills_then_balance(deps, params: dict, progress) -> dict:
    """큐 `fills_sync`(체결통보·웹 버튼) 본문: 동기화 직후 잔고도 바로 다시 읽는다(장 밖이어도).
    잔고 실패는 체결 결과를 가리지 않는다."""
    out = sync_all(deps)
    if deps.ports.get("accounts") is None:
        return out
    try:
        out["balance"] = balance_refresh(deps, force=True)
    except Exception as e:  # noqa: BLE001 - 잔고는 보조, 다음 주기 갱신이 다시 읽는다
        logger.warning("체결 동기화 뒤 잔고 갱신 실패: %s", e)
    return out


def reconcile_run(ctx, market: str | None) -> dict:
    """야간 대조: 체결부터 맞춘 뒤(US 대조가 정기 체결 동기화보다 앞서는 순서 의존을 잡 안에서
    끊는다) 그 시장(없으면 전체)의 모든 계좌를 브로커 값과 대조한다."""
    if ctx.ports.get("accounts") is None:
        return {"error": "계좌 포트 없음 — 대조 불가"}
    try:
        sync = sync_all(ctx)
        if sync.get("error"):
            logger.warning("대조 전 체결 동기화 일부 실패: %s", sync["error"])
    except Exception as e:  # noqa: BLE001 - 동기화 실패여도 대조는 브로커 값으로 돈다
        logger.warning("대조 전 체결 동기화 실패: %s", e)
    conn = ctx.conn_factory()
    try:
        notifier = ctx.notifier or NullNotifier()
        ectx = executor.exec_ctx_from(
            ctx.settings, conn, ctx.ports.get("accounts"), notifier, ctx.now, ctx.state
        )
        return reconcile.reconcile_all(
            conn,
            ectx.brokers,
            ectx.accounts,
            market=market,
            today_for=lambda m: run_date_for(m, ctx.now()),
            now=ctx.now,
            notifier=notifier,
        )
    finally:
        conn.close()


def reconcile_body(ctx, job) -> dict:
    """`{market}_reconcile` 크론 본문."""
    return reconcile_run(ctx, job.market)


def reconcile_request(deps, params: dict, progress) -> dict:
    """큐 `reconcile` 본문: 전 시장 또는 지정 시장."""
    return reconcile_run(deps, params.get("market"))


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


FILLS_SYNC = JobSpec(
    id="fills_sync", kind="fills_sync", trigger="cron", per_market=False,
    schedule_key="fills_sync.times", interval_s=None, enabled=None, gate="none", heavy=False,
    misfire="idempotent", catch_up="queue", start_soon_s=0, body=fills_body, days="mon-sat",
)  # fmt: skip

BALANCE_REFRESH = JobSpec(
    id="balance_refresh", kind="balance_refresh", trigger="interval", per_market=False,
    schedule_key="balance_refresh.interval_s", interval_s=300, enabled=None, gate="none",
    heavy=False, misfire="idempotent", catch_up="none", start_soon_s=0, body=balance_body,
)  # fmt: skip

RECONCILE = JobSpec(
    id="{market}_reconcile", kind="reconcile", trigger="cron", per_market=True,
    schedule_key="reconcile.{market}", interval_s=None, enabled=None, gate="market", heavy=False,
    misfire="idempotent", catch_up="queue", start_soon_s=0, body=reconcile_body, days="mon-fri",
)  # fmt: skip

REBALANCE = JobSpec(
    id="{market}_rebalance", kind="rebalance", trigger="cron", per_market=True,
    schedule_key="rebalance.{market}", interval_s=None, enabled=lambda s: s.rebalance.enabled,
    gate="market", heavy=False, misfire="idempotent", catch_up="queue", start_soon_s=0,
    body=rebalance.rebalance_body, days="fri",
)  # fmt: skip


def _open_hm(ctx, market: str) -> tuple[int, int]:
    """장 시작 시각(schedule `<시장>.monitor.start`, 없으면 정규장 개장)."""
    start = REGULAR[market][0]
    sched = getattr(ctx, "schedule", None)
    if sched is not None:
        start = getattr(getattr(sched, market.lower()).monitor, "start", start)
    h, m = start.split(":")
    return int(h), int(m)


def _reprice_if_due(ctx, ectx, market: str, today) -> dict:
    """아침 미체결 재호가(Phase 23 B): 개장 + `risk.reprice.after_min` 뒤 운영일 1회."""
    cfg = ctx.settings.risk.reprice
    if not cfg.enabled or ectx.market_client is None or ctx.dry_run:
        return {"skipped": "비활성"}
    local = ctx.now().astimezone(MARKET_TZ[market])
    oh, om = _open_hm(ctx, market)
    if (local.hour * 60 + local.minute) - (oh * 60 + om) < cfg.after_min:
        return {"skipped": "개장 뒤 대기"}
    if control.get_flag(ectx.conn, reprice.done_key(market, today)):
        return {"skipped": "오늘 이미 실행"}
    mc = ectx.market_client

    def quote(ticker: str, exchange: str | None):
        # 시세 객체 그대로 — 재제출 게이트가 등락률·종목상태를 본다(2-O5)
        if market == "US":
            if not exchange:
                raise ValueError(f"US 거래소 코드 없음: {ticker}")
            return mc.get_price_us(ticker, exchange)
        return mc.get_price_kr(ticker)

    exchanges = spot_ledger.ticker_exchanges(ectx.conn, market) if market == "US" else {}
    return reprice.run_once(
        ectx,
        market=market,
        today=today,
        cfg=cfg,
        quote_fn=quote,
        exchanges=exchanges,
        dry_run=bool(ctx.dry_run),
        label=lambda t: labels.label(t, labels.resolve(ectx.conn, market, t)),
    )


def _hedge_if_due(ctx, market: str) -> dict | None:
    """국면 헤지 틱(`hedge.hedge_tick`, 운영일 1회). 건너뜀(`skipped`)이면 None — 결과에 싣지
    않는다. 예외는 `[헤지 실패]` 알림 + `{"error": …}` 로 돌려 손절 점검을 막지 않는다."""
    try:
        out = hedge.hedge_tick(ctx, market)
    except Exception as e:  # noqa: BLE001 - 헤지 실패가 손절 점검을 막지 않는다
        logger.exception("헤지 실행 실패 %s", market)
        note = messages.failure("헤지 실패", market, e)
        core.notify_quietly(ctx.notifier or NullNotifier(), note)
        return {"error": f"{type(e).__name__}: {e}"}
    return None if "skipped" in out else out


def _cash_park_if_due(ctx, market: str) -> dict | None:
    """현금 단기금리 ETF 운용 틱(`cash_park.cash_park_tick`, KR·`cash_park.enabled` 일 때만).
    건너뜀(`skipped`)이면 None. 예외는 로그 + `{"error": …}`(알림 없음 — 옛 동작)로 돌려 손절
    점검을 막지 않는다."""
    if market != "KR" or not ctx.settings.cash_park.enabled:
        return None
    try:
        out = cash_park.cash_park_tick(ctx)
    except Exception as e:  # noqa: BLE001 - 현금 운용 실패가 손절 점검을 막지 않는다
        logger.exception("현금 운용 실패")
        return {"error": f"{type(e).__name__}: {e}"}
    return None if "skipped" in out else out


def monitor_body(ctx, job) -> dict:
    """`{market}_monitor` interval 잡 본문(장중 창은 게이트 `monitor` 가 본다). 재평가 세션은 틱
    안에서 돌리지 않고 큐 kind `reevaluate` 로 넘긴다 — 세션이 도는 동안 손절 점검이 멈추면 안
    된다. 운영일은 시장 현지(US 는 뉴욕 날짜). 손절 점검 전에 국면 헤지(`hedge`, 운영일 1회)·
    현금 운용(`cash_park`, KR)·아침 재호가(`reprice`)를 시도하고,
    건너뛰지 않았으면 결과에 그 키로 싣는다."""
    market = job.market or "KR"
    port = ctx.ports.get("accounts")
    if port is None:
        return {"error": "계좌 포트 없음 — 장중 감시 불가"}
    conn = ctx.conn_factory()
    try:
        ectx = executor.exec_ctx_from(
            ctx.settings, conn, port, ctx.notifier or NullNotifier(), ctx.now, ctx.state,
            ports=ctx.ports,
        )
        if ectx.market_client is None:
            return {"skipped": "시세 클라이언트 없음"}
        today = run_date_for(market, ctx.now())
        extra: dict[str, Any] = {}
        hd = _hedge_if_due(ctx, market)
        if hd is not None:
            extra["hedge"] = hd
        park = _cash_park_if_due(ctx, market)
        if park is not None:
            extra["cash_park"] = park
        try:  # 재호가 실패가 손절 점검을 막지 않는다
            rp = _reprice_if_due(ctx, ectx, market, today)
            if not isinstance(rp.get("skipped"), str):  # 건너뜀 사유(문자열)는 싣지 않는다
                extra["reprice"] = rp
        except Exception as e:  # noqa: BLE001
            logger.exception("재호가 실패 %s", market)
            extra["reprice"] = {"error": f"{type(e).__name__}: {e}"}
        mctx = monitor.MonitorContext(
            exec=ectx,
            market_client=ectx.market_client,
            session_runner=run_session,
            enqueue_reevaluate=lambda params: enqueue_from_job(
                ctx, "reevaluate", params, source="daemon"
            ),
            exchanges=spot_ledger.ticker_exchanges(conn, market) if market == "US" else {},
            dry_run=bool(ctx.dry_run),
            ports=ctx.ports,
            close_table=ml.close_table if (ml := ctx.ports.get("ml")) is not None else None,
        )
        return {**monitor.monitor_once(mctx, market=market, today=today), **extra}
    finally:
        conn.close()


MONITOR = JobSpec(
    id="{market}_monitor", kind="monitor", trigger="interval", per_market=True,
    schedule_key="{market}.monitor.interval_s", interval_s=60, enabled=None, gate="monitor",
    heavy=False, misfire="default", catch_up="none", start_soon_s=20, body=monitor_body,
)  # fmt: skip


class ReconcileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str | None = None


class RebalanceParams(BaseModel):
    """리밸런스 요청. market 이 없으면 데몬 시장 전체."""

    model_config = ConfigDict(extra="forbid")

    market: Literal["KR", "US"] | None = None


# --- 큐 reevaluate (옛 daemon `_reevaluate_request`) ----------------------------------


class ReevaluateParams(BaseModel):
    """재평가 요청. 모양은 `monitor._enqueue` 가 넣는 것과 같다. dry_run 칸은 없다 — 데몬의
    `--dry-run` 을 요청이 뒤집지 못한다."""

    model_config = ConfigDict(extra="forbid")

    market: str
    ticker: str
    exchange: str | None = None
    trigger_id: int
    trigger_ids: list[int] = []


def _recollect(deps, market: str, ticker: str, exchange: str | None) -> None:
    """뉴스 재평가: 입력을 강제 재수집한다. 포트가 없거나 실패하면 기존 입력으로 재평가."""
    inputs = deps.ports.get("inputs")
    if inputs is None:
        logger.warning("뉴스 재평가 재수집 생략 %s %s: 수집 포트 없음", market, ticker)
        return
    market_client = None
    accounts = deps.ports.get("accounts")
    if accounts is not None:
        try:
            market_client = accounts.market_client()
        except Exception as e:  # noqa: BLE001 - 시세 없이도 수집은 돈다
            logger.warning("뉴스 재평가 시세 클라이언트 확인 실패: %s", type(e).__name__)
    try:
        inputs.collect(
            ticker=ticker,
            market=market,
            exchange=exchange,
            asof=deps.now(),
            force=True,
            conn_factory=deps.conn_factory,
            market_client=market_client,
            state=deps.state,
        )
    except Exception as e:  # noqa: BLE001 - 재수집 실패면 기존 입력으로 재평가
        logger.warning("뉴스 재평가 재수집 실패 %s %s: %s", market, ticker, e)


def reevaluate_request(deps, params: dict, progress) -> dict:
    """큐 `reevaluate` 1건: 트리거 행을 읽어 `resume.reevaluate` 세션을 돈다. 이미 처리된
    트리거·장 마감(장 없는 날 포함)·종료 중이면 세션 없이 건너뛴다. 세션은 같은 종목 분석과
    겹치지 않게 종목 락(`locks.lock_for`) 안에서 돌고, 끝나면 형제 트리거도 같은 표시로 닫는다."""
    conn = deps.conn_factory()
    try:  # 세션 전에 stale running(1시간 초과) 세션 행을 error 로 닫는다
        calls.close_stale(conn, now=deps.now())
    finally:
        conn.close()
    market, ticker = params["market"], params["ticker"]
    exchange = params.get("exchange")
    trigger_id = int(params["trigger_id"])
    tids = [trigger_id] + [int(t) for t in params.get("trigger_ids") or []]
    conn = deps.conn_factory()
    try:
        trig = spot_ledger.get_trigger(conn, trigger_id)
        if trig is None:
            return {"error": f"트리거 없음 #{trigger_id}"}
        if trig.get("handled_at"):
            return {"skipped": "이미 처리된 트리거"}
        if session_over(market, deps.now()):
            return {"skipped": "장 마감"}
        today = run_date_for(market, deps.now())
        if trig["kind"] == "news":
            # 뉴스 재평가(Phase 23): 트레이더가 새 뉴스를 보도록 00_inputs 를 다시 수집한다
            _recollect(deps, market, ticker, exchange)
        if deps.stop_event.is_set():  # 재수집(최대 수 분) 사이 종료 요청이면 세션을 띄우지 않는다
            return {"skipped": "종료 중"}
        lane = LaneContext(
            settings=deps.settings,
            conn=conn,
            state=deps.state,
            now=deps.now,
            dry_run=bool(deps.dry_run),
            notifier=deps.notifier or NullNotifier(),
            ports=deps.ports,
        )
        try:
            with locks.lock_for(market, ticker):  # 같은 종목 analyze 와 겹치지 않게
                res = resume.reevaluate(
                    lane,
                    market=market,
                    date=today,
                    ticker=ticker,
                    exchange=exchange,
                    trigger_id=trigger_id,
                    trigger={
                        "kind": trig["kind"],
                        "price": str(trig["price"]) if trig.get("price") is not None else None,
                        "detail": trig.get("detail") or {},
                    },
                    session_runner=run_session,
                    cancel=deps.stop_event,
                )
        except Exception as e:  # 기동 실패: 트리거를 '실패:' 로 닫아 30분 뒤 재시도하게 한다
            logger.exception("재평가 세션 기동 실패 %s %s", market, ticker)
            # 정리(트리거 마감·종목 표기)가 다시 실패해도 원래 예외가 알림·요청 오류로 가야 한다
            try:
                for tid in tids:
                    spot_ledger.mark_trigger_handled(
                        conn, tid, f"실패: {type(e).__name__}: {e}"[:200],
                        handled_at=deps.now().isoformat(),
                    )  # fmt: skip
            except Exception as ee:  # noqa: BLE001
                logger.warning("재평가 실패 트리거 마감 실패 %s %s: %s", market, ticker, ee)
            try:
                label = labels.label(ticker, labels.resolve(conn, market, ticker))
            except Exception as ee:  # noqa: BLE001
                logger.warning("종목 표기 실패 %s %s: %s", market, ticker, ee)
                label = ticker
            core.notify_quietly(
                lane.notifier, messages.reevaluation_failed(market=market, label=label, err=e)
            )
            raise
        for tid in tids[1:]:  # 같은 세션이 나머지 트리거도 처리한 것으로 표시(실패도 같은 표시)
            spot_ledger.mark_trigger_handled(
                conn, tid, resume.handled_marker(res), handled_at=deps.now().isoformat()
            )
    finally:
        conn.close()
    out: dict[str, Any] = {"ticker": ticker, "ok": res.ok, "session_id": res.session_id}
    if not res.ok:
        out["error"] = res.error or "재평가 세션 실패"
    return out


class ApproveExecuteParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: int
    actor: str | None = None


def _not_run(aid: int, status: str, **why: str) -> dict:
    return {"ok": False, "approval_id": aid, "status": status, **why}


def approve_execute_body(deps, params: dict, progress) -> dict:
    """큐 `approve_execute`: `approvals.decide` 로 선점된 승인 1건을 집행한다(웹은 주문하지 않고
    이 요청만 넣는다). 결정 없이 들어온 요청(`/api/jobs`·CLI 직접 등록)은 실전 확인을 우회하므로
    거부하고, 이미 처리된 승인은 `{"status": "done"}` 으로 건너뛴다(멱등)."""
    aid = int(params["approval_id"])
    conn = deps.conn_factory()
    try:
        a = xledger.get_approval(conn, aid)
        if a is None:
            return _not_run(aid, "missing", error=f"승인 요청 #{aid} 없음")
        if a["status"] != "pending":
            return _not_run(aid, "done", skipped=f"승인 요청 #{aid} 은 이미 처리됨: {a['status']}")
        if not a.get("decided_channel"):
            return _not_run(
                aid, "not_decided", error=f"승인 요청 #{aid} 은 웹·텔레그램 결정 전이라 집행 안 함"
            )
        port = deps.ports.get("accounts")
        if port is None:
            approvals.release(conn, aid)
            return _not_run(aid, "pending", error="계좌 포트 없음 — 집행 불가(결정은 풀었어요)")
        progress(f"승인 #{aid} 집행")  # 종료 중이면 여기서 JobCancelled — 주문 전에 멈춘다
        ectx = executor.exec_ctx_from(
            deps.settings, conn, port, deps.notifier or NullNotifier(), deps.now, deps.state,
            ports=deps.ports, dry_run=bool(deps.dry_run),
        )  # fmt: skip
        return approvals.execute(ectx, aid)
    finally:
        conn.close()


class ApprovalNoticeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: int
    action: Literal["reject"]
    actor: str | None
    channel: str


def approval_notice_body(deps, params: dict, progress) -> dict:
    """큐 `approval_notice`: 웹 거부 뒤 텔레그램 `[거부]` 알림(spot `messages.reject_notice` —
    `/reject` 와 같은 문구)을 보낸다. 웹은 알림을 쥐지 않아 데몬이 보낸다(P15). 알림기가 없거나
    승인이 거부 상태가 아니면 건너뛴다."""
    aid = int(params["approval_id"])
    if deps.notifier is None:
        return {"approval_id": aid, "skipped": "알림기 없음"}
    conn = deps.conn_factory()
    try:
        a = xledger.get_approval(conn, aid)
    finally:
        conn.close()
    if a is None:
        return {"approval_id": aid, "skipped": f"승인 요청 #{aid} 없음"}
    if a["status"] != "rejected":  # 거부로 끝난 승인에만 [거부] 알림을 보낸다(M-1)
        return {"approval_id": aid, "skipped": f"승인 요청 #{aid} 상태 {a['status']}"}
    port = (deps.ports or {}).get("accounts")
    acct = next((x for x in port.accounts() if x.id == a["account_id"]), None) if port else None
    ok = send(deps.notifier, reject_notice(a, acct.env if acct is not None else None))
    return {"approval_id": aid, "sent": bool(ok)}


APPROVE_EXECUTE = QueueKind(
    "approve_execute", ApproveExecuteParams, "승인 집행", "fast", 0, True, False,
    approve_execute_body,
    # 대기 중 집행 요청을 취소하면 그 승인의 선점을 푼다(P14) — 다시 결정할 수 있게
    on_cancel=lambda conn, p: approvals.release(conn, int(p["approval_id"])),
)  # fmt: skip
APPROVAL_NOTICE = QueueKind(
    "approval_notice", ApprovalNoticeParams, "승인 결정 알림", "info", 0, False, False,
    approval_notice_body,
)  # fmt: skip

QUEUE_KINDS = (
    APPROVE_EXECUTE,
    APPROVAL_NOTICE,
    QueueKind(
        "fills_sync", EmptyParams, "체결 동기화", "fast", 0, False, True, fills_then_balance
    ),
    QueueKind(
        "reconcile", ReconcileParams, "대사", "fast", 0, False, True, reconcile_request
    ),
    QueueKind(
        "rebalance", RebalanceParams, "리밸런스", "fast", 0, True, False,
        rebalance.rebalance_request,
    ),
    QueueKind(
        "reevaluate", ReevaluateParams, "재평가", "reeval", 10, True, False, reevaluate_request
    ),
    *QUEUE_KINDS_SCAN,
)


# --- 체결통보 리스너 ------------------------------------------------------------------------

# env → 리스너. 데몬 수명 동안 env 마다 하나(옛 데몬 `notice_listeners` 와 같은 수명).
# teardown(`notice_stop`)이 멈추고 비운다
_LISTENERS: dict[str, notice.NoticeListener] = {}
_LISTENERS_LOCK = threading.Lock()


def notice_handler(ctx) -> Callable[[str], bool]:
    """체결통보 → 작업 대기열 `fills_sync`(빠른 레인). 이미 **대기 중**이면 충분하다(True).
    **실행 중**이면 그 실행이 이 통보 전 조회였을 수 있어 False — 리스너가 미뤄 두고 다시 요청."""

    def on_notice(env: str) -> bool:
        rid = enqueue_from_job(ctx, "fills_sync", {}, source=f"notice:{env}")
        if rid is not None:
            logger.info("체결통보(%s) → 체결 동기화 요청 #%s", env, rid)
            return True
        conn = ctx.conn_factory()
        try:
            # 중복 검사(queued|running)를 통과 못 했다 — 활성 요청은 하나뿐이라 최신 1건이 그것
            last = jobq.latest_by(conn, "fills_sync")
        finally:
            conn.close()
        return last is not None and last["status"] == "queued"

    return on_notice


def _auth_for(creds: Any) -> Any:
    """앱키별 KIS 인증(접속키 `approval_key`·`invalidate_approval`). 생성 시 네트워크 없음."""
    return factory.client_for(creds).auth


def _notice_enabled(ctx, port: Any, env: str, members: list) -> Callable[[], bool]:
    """모의는 늘 켬. 실전은 그 HTS ID 의 실전 계좌가 실제로 주문을 낼 수 있을 때(유효 모드가
    off 아님)만 — 실전 앱키의 웹소켓 세션을 화면 시세 허브와 나눠 쓸 수 있기 때문이다."""
    if env != "real":
        return lambda: True

    def enabled() -> bool:
        conn = ctx.conn_factory()
        try:
            return any(port.policy(conn, a)[0] != "off" for a in members)
        finally:
            conn.close()

    return enabled


def _market_session_client(port: Any) -> Any | None:
    """시세 허브가 쓰는 KIS 클라이언트(없으면 None). `client_for` 가 (env, 앱키)마다 하나만 만들므로
    같은 앱키면 같은 객체다 — 키 값을 꺼내지 않고 객체 동일성으로 비교한다."""
    try:
        mc = port.market_client()
    except Exception as e:  # noqa: BLE001 - 시세 없이도 리스너는 붙는다
        logger.warning("체결통보: 시세 클라이언트 확인 실패: %s", type(e).__name__)
        return None
    return getattr(mc, "client", None) if mc is not None else None


def _shares_market_key(creds: Any, market_client: Any | None) -> bool:
    if market_client is None or creds.env != "real":
        return False
    try:
        return factory.client_for(creds) is market_client
    except Exception:  # noqa: BLE001 - 클라이언트를 못 만들면 겹치지 않는 것으로 본다
        return False


def _notice_targets(ctx, port: Any) -> dict[str, tuple[str, Any, list, bool]]:
    """env → (HTS ID, 자격증명, 같은 HTS ID 계좌들, 시세 키 공유 여부). HTS ID·앱키가 있는 현물
    계좌만(없으면 그 env 는 정기 동기화만). 통보는 HTS ID 단위라 같은 명의 계좌 여럿은 연결 하나로
    받는다. KIS 는 앱키당 웹소켓 세션 하나라, 실전은 시세 허브가 쓰지 않는 계좌 전용 키를 먼저
    고른다(충돌 없음). 같은 키밖에 없으면 공유 — 붙어 있는 동안 허브가 비켜선다."""
    groups: dict[str, list] = {}
    for a in sorted(port.accounts(), key=lambda x: x.id):
        if getattr(a, "asset", "stock") != "stock":
            continue
        c = ctx.secrets.kis_creds(a)
        if c.hts_id and c.app_key and c.app_secret:
            groups.setdefault(a.env, []).append((a, c))
    market_client = _market_session_client(port) if "real" in groups else None
    out: dict[str, tuple[str, Any, list, bool]] = {}
    for env, members in groups.items():
        hts = members[0][1].hts_id
        same = [(a, c) for a, c in members if c.hts_id == hts]
        if len(same) < len(members):
            logger.warning(
                "체결통보 (%s): HTS ID 가 여럿 — 첫 계좌 %s 의 것만 구독", env, same[0][0].id
            )
        # 시세 허브와 겹치지 않는 키를 먼저(같은 키면 허브가 비켜선다)
        ranked = sorted(same, key=lambda m: (_shares_market_key(m[1], market_client), m[0].id))
        creds = ranked[0][1]
        shares = _shares_market_key(creds, market_client)
        out[env] = (hts, creds, [a for a, _ in same], shares)
    return out


def notice_listeners(ctx, skip: frozenset[str] = frozenset()) -> list[notice.NoticeListener]:
    """(env, HTS ID) 마다 체결통보 리스너 하나(시작 전). skip 의 env 는 만들지 않는다."""
    port = ctx.ports.get("accounts")
    if port is None or getattr(ctx, "secrets", None) is None:
        return []
    out: list[notice.NoticeListener] = []
    for env, (hts, creds, members, shares) in sorted(_notice_targets(ctx, port).items()):
        if env in skip:
            continue
        try:
            auth = _auth_for(creds)
        except Exception as e:  # noqa: BLE001 - 한 env 실패가 다른 env 를 막지 않는다
            logger.warning("체결통보 생략 (%s): %s", env, type(e).__name__)
            continue
        lst = notice.NoticeListener(
            env=env,
            hts_id=hts,
            approval=auth.approval_key,
            invalidate=auth.invalidate_approval,
            on_notice=notice_handler(ctx),
            enabled=_notice_enabled(ctx, port, env, members),
            conn_factory=ctx.conn_factory,
        )
        lst.shares_market_session = shares
        out.append(lst)
    return out


def notice_body(ctx, job) -> dict:
    """`notice_listener` interval 잡 본문: env 별 리스너 1개가 돌고 있음을 보장한다(죽었으면 다시
    띄우고, HTS ID 거절로 멈춘 리스너는 그대로 둔다). 반환 {listeners: {env: status}}.

    전제: 데몬은 종료 때 `stop_event` 를 먼저 세우고, 스케줄러(이 interval 잡 실행 포함)를 내린 뒤
    teardown(`notice_stop`)을 부른다 — 그래서 teardown 이 `_LISTENERS` 를 비운 뒤 이 본문이 다시
    리스너를 띄울 여지가 없다(혹시 겹쳐도 아래 `stop_event` 검사가 막는다)."""
    stop = getattr(ctx, "stop_event", None)
    if stop is not None and stop.is_set():
        return {"skipped": "종료 중", "listeners": {}}
    if ctx.ports.get("accounts") is None or getattr(ctx, "secrets", None) is None:
        return {"skipped": "계좌 포트·시크릿 없음 — 정기 체결 동기화만", "listeners": {}}
    with _LISTENERS_LOCK:
        keep = frozenset(
            env for env, cur in _LISTENERS.items() if cur.alive() or cur.rejected is not None
        )
        for lst in notice_listeners(ctx, skip=keep):
            _LISTENERS[lst.env] = lst
            lst.start()
        return {"listeners": {env: lst.status() for env, lst in sorted(_LISTENERS.items())}}


def notice_stop(ctx) -> None:
    """데몬 종료(teardown): 모든 리스너를 멈추고 스레드가 끝나기를 기다린다.

    전제: 커널이 teardown 전에 `stop_event` 를 세우고 interval 잡 실행이 끝난 뒤 부른다 — 비운
    `_LISTENERS` 에 `notice_body` 가 리스너를 다시 띄우지 않는다."""
    with _LISTENERS_LOCK:
        listeners = list(_LISTENERS.values())
        _LISTENERS.clear()
    for lst in listeners:
        try:
            lst.stop()
        except Exception:  # noqa: BLE001 - 하나의 종료 실패가 나머지를 막지 않는다
            logger.exception("체결통보 리스너 종료 실패 (%s)", lst.env)


NOTICE_LISTENER = JobSpec(
    id="notice_listener", kind="notice_listener", trigger="interval", per_market=False,
    schedule_key=None, interval_s=30, enabled=lambda s: s.fill_notice.enabled, gate="none",
    heavy=False, misfire="default", catch_up="none", start_soon_s=5, body=notice_body,
    teardown=notice_stop,
)  # fmt: skip
