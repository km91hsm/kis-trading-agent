"""spot 데몬 잡: `approvals_expire`(만료 승인 정리)·`fills_sync`(체결 동기화)·`balance_refresh`·
`{market}_reconcile`(야간 대사)·`{market}_monitor`(장중 감시)·`notice_listener`(체결통보)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from trader.features.spot import (
    balance,
    executor,
    fills,
    labels,
    monitor,
    notice,
    reconcile,
    reprice,
)
from trader.kernel.broker import factory
from trader.kernel.config.market_hours import REGULAR
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.queue_jobs import enqueue_from_job
from trader.kernel.ledger import control
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.registry.types import JobSpec, QueueKind
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
    misfire="idempotent", catch_up="queue", start_soon_s=0, body=reconcile_body, days="mon-fri",
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

    exchanges: dict[str, str] = {}
    if market == "US":
        rows = ectx.conn.execute("SELECT ticker, exchange FROM ticker_exchanges WHERE market='US'")
        for r in rows:
            exchanges[r["ticker"]] = r["exchange"]
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


def monitor_body(ctx, job) -> dict:
    """`{market}_monitor` interval 잡 본문(장중 창은 게이트 `monitor` 가 본다). 재평가 세션은 틱
    안에서 돌리지 않고 큐 kind `reevaluate` 로 넘긴다 — 세션이 도는 동안 손절 점검이 멈추면 안
    된다. 운영일은 시장 현지(US 는 뉴욕 날짜)."""
    market = job.market or "KR"
    port = ctx.ports.get("accounts")
    if port is None:
        return {"error": "계좌 포트 없음 — 장중 감시 불가"}
    conn = ctx.conn_factory()
    try:
        ectx = executor.exec_ctx_from(
            ctx.settings, conn, port, ctx.notifier or NullNotifier(), ctx.now, ctx.state
        )
        if ectx.market_client is None:
            return {"skipped": "시세 클라이언트 없음"}
        today = run_date_for(market, ctx.now())
        extra: dict[str, Any] = {}
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
            dry_run=bool(ctx.dry_run),
            ports=ctx.ports,
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


QUEUE_KINDS = (
    QueueKind(
        "fills_sync", EmptyParams, "체결 동기화", "fast", 0, False, True, fills_then_balance
    ),
    QueueKind(
        "reconcile", ReconcileParams, "대사", "fast", 0, False, True, reconcile_request
    ),
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


def _notice_targets(ctx, port: Any) -> dict[str, tuple[str, Any, list]]:
    """env → (HTS ID, 자격증명, 같은 HTS ID 계좌들). HTS ID·앱키가 있는 현물 계좌만(없으면 그
    env 는 정기 동기화만). 통보는 HTS ID 단위라 같은 명의 계좌 여럿은 연결 하나로 받는다."""
    groups: dict[str, list] = {}
    for a in sorted(port.accounts(), key=lambda x: x.id):
        if getattr(a, "asset", "stock") != "stock":
            continue
        c = ctx.secrets.kis_creds(a)
        if c.hts_id and c.app_key and c.app_secret:
            groups.setdefault(a.env, []).append((a, c))
    out: dict[str, tuple[str, Any, list]] = {}
    for env, members in groups.items():
        hts, creds = members[0][1].hts_id, members[0][1]
        same = [a for a, c in members if c.hts_id == hts]
        if len(same) < len(members):
            logger.warning(
                "체결통보 (%s): HTS ID 가 여럿 — 첫 계좌 %s 의 것만 구독", env, same[0].id
            )
        out[env] = (hts, creds, same)
    return out


def notice_body(ctx, job) -> dict:
    """`notice_listener` interval 잡 본문: env 별 리스너 1개가 돌고 있음을 보장한다(죽었으면 다시
    띄우고, HTS ID 거절로 멈춘 리스너는 그대로 둔다). 반환 {listeners: {env: status}}."""
    stop = getattr(ctx, "stop_event", None)
    if stop is not None and stop.is_set():
        return {"skipped": "종료 중", "listeners": {}}
    port = ctx.ports.get("accounts")
    if port is None or getattr(ctx, "secrets", None) is None:
        return {"skipped": "계좌 포트·시크릿 없음 — 정기 체결 동기화만", "listeners": {}}
    targets = _notice_targets(ctx, port)
    with _LISTENERS_LOCK:
        for env, (hts, creds, members) in sorted(targets.items()):
            cur = _LISTENERS.get(env)
            if cur is not None and (cur.alive() or cur.rejected is not None):
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
            )
            _LISTENERS[env] = lst
            lst.start()
        return {"listeners": {env: lst.status() for env, lst in sorted(_LISTENERS.items())}}


def notice_stop(ctx) -> None:
    """데몬 종료(teardown): 모든 리스너를 멈추고 스레드가 끝나기를 기다린다."""
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
