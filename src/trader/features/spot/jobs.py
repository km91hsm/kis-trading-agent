"""spot 데몬 잡: `approvals_expire`(만료 승인 정리)와 `fills_sync`(체결 동기화)."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from trader.features.spot import balance, executor, fills, reconcile
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.registry.types import JobSpec, QueueKind
from trader.kernel.session.run_store import run_date_for

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
    보다 오래됐을 때만(입출금·이체 반영). force 면 장 밖이어도 바로 읽는다."""
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
            ctx.settings, conn, ctx.ports.get("accounts"), ctx.notifier, ctx.now, ctx.state
        )
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
    misfire="idempotent", catch_up="queue", start_soon_s=0, body=reconcile_body,
)  # fmt: skip


class ReconcileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str | None = None


QUEUE_KINDS = (
    QueueKind(
        "fills_sync", EmptyParams, "체결 동기화", "fast", 0, False, True, fills_then_balance
    ),
    QueueKind(
        "reconcile", ReconcileParams, "대사", "fast", 0, False, True, reconcile_request
    ),
)
