"""fo 데몬 잡 3개·큐 kind 4개와 본문(A5, 옛 `daemon.py` 의 `kr_fo_session`·`kr_fo_monitor` 등록과
`_fo_session_body`·`_fo_monitor_job`·`_fo_reevaluate_request`·`_fo_close_request`·
`_release_fo_trigger`, 옛 `jobs.py` 의 `FoReevaluateParams`·`FoCloseParams`).

잡은 전부 KR 전용·`per_market=False`·`gate="none"` 이다 — `monitor` 게이트는 `schedule.kr.monitor`
창에 묶여 15:30~15:45 를 놓치고, `market` 게이트는 `job.market` 이 필요하다. 그래서 본문이 직접
거른다: 파생 계좌 없음 → "파생 계좌 없음", 일시정지·주말·휴장(커널 `daemon.gates.should_skip`,
휴장은 `ports["accounts"].holiday_fn`) → 옛 `market_job` 과 같은 사유, 모니터는 추가로
`is_fo_open(now)` ∧ `schedule.fo.kr.monitor` 창 안이어야 한다("파생 창 밖").

큐 본문 `(deps, params, progress)`: 커널 `auto_gate` 는 시장 게이트만 보므로 옛 `_auto_gate` 의 파생
시계 분기는 본문으로 옮겼다 — `fo_reevaluate`·`fo_close` 는 파생 장 밖이면 "파생 장 밖", 재평가는
일시정지·휴장도 보고 막히면 트리거를 `보류: <사유>` 로 닫는다(미처리로 두면 그날 그 계약의 새
트리거가 전부 막힌다). `fo_session` 은 옛처럼 파생 시계를 보지 않는다(08:20 세션은 장 전).
세션 상한(`fo.session_cap`)은 `session.run` 이 커널 `PipelineError` 로 올리고 여기서 skipped 로
바꾼다.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trader.features.fo import balance as fo_balance
from trader.features.fo import engine, executor, reeval, store
from trader.features.fo import fills as fo_fills
from trader.features.fo import session as fo_session
from trader.features.fo.executor import context_sources
from trader.features.fo.types import FoExecContext
from trader.kernel.broker import fo_master
from trader.kernel.config import KST
from trader.kernel.config.market_hours import in_window, is_fo_open
from trader.kernel.daemon.gates import should_skip
from trader.kernel.execution import ledger as xledger
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.platform import paths
from trader.kernel.registry.types import JobSpec, QueueKind
from trader.kernel.session.run_store import run_date_for
from trader.kernel.session.runner import PipelineError

logger = logging.getLogger("trader.fo.jobs")

MARKET = "KR"
NO_ACCOUNTS = "파생 계좌 없음"
OUT_OF_WINDOW = "파생 창 밖"
FO_CLOSED = "파생 장 밖"
_ID = r"^[A-Za-z0-9_\-]+$"


# --- 큐 파라미터 ------------------------------------------------------------------------------


class EmptyParams(BaseModel):
    """파라미터 없는 요청(`fo_session` — 계좌·시각은 데몬 설정이 정한다)."""

    model_config = ConfigDict(extra="forbid")


class FoReevaluateParams(BaseModel):
    """파생 장중 재평가(엔진이 트리거·요청 체크포인트를 남긴 뒤 넣는다, `reeval.run`)."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=40, pattern=_ID)
    position_id: int = Field(ge=1)
    expected_version: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=64, pattern=_ID)
    round: int = Field(ge=1, le=99)
    trigger_id: int = Field(ge=1)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class FoCloseParams(BaseModel):
    """파생 포지션 수동 청산(실행 직전 모드·장 시간·상태를 다시 본다)."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=40, pattern=_ID)
    position_id: int = Field(ge=1)


# --- 공용 -----------------------------------------------------------------------------------


def _fo_accounts(deps: Any) -> list[Any]:
    acc = (deps.ports or {}).get("accounts")
    return [a for a in acc.fo_accounts() if a.asset == "fo"] if acc is not None else []


def _day_skip(deps: Any, *, ignore_paused: bool = False) -> str | None:
    """옛 `market_job` 게이트: 일시정지·주말·휴장일(KR 운영일 기준) 사유 또는 None.
    `ignore_paused` 면 일시정지는 사유가 아니다(모니터 — 위험 청산은 일시정지 중에도 돈다)."""
    acc = deps.ports["accounts"]
    holiday = acc.holiday_fn()
    conn = deps.conn_factory()
    try:
        return should_skip(
            MARKET, run_date_for(MARKET, deps.now()),
            paused=(lambda: False) if ignore_paused else (lambda: bool(acc.paused(conn))),
            is_holiday=(lambda d: bool(holiday(MARKET, d))) if holiday else (lambda d: False),
        )  # fmt: skip
    finally:
        conn.close()


def _fo_window(deps: Any) -> bool:
    """파생 정규장 ∧ `schedule.fo.kr.monitor` 창(KST) 안."""
    now = deps.now()
    win = deps.schedule.fo.kr.monitor
    local = now.astimezone(KST).time().replace(second=0, microsecond=0)
    return is_fo_open(now) and in_window(local, win.start, win.end)


def _master() -> list:
    try:
        return fo_master.load(cache_dir=paths.state_dir() / "fo")
    except Exception as e:  # noqa: BLE001 — 마스터 없이도 보유분 감시는 돈다(신규는 게이트가 거부)
        logger.warning("파생 마스터 적재 실패(빈 목록): %s", e)
        return []


def _fo_ctx(deps: Any, conn: sqlite3.Connection) -> FoExecContext:
    """데몬 잡·큐 본문용 파생 실행 컨텍스트. 계좌·브로커·시세는 accounts 포트, 국면·시장 뷰는
    regime 포트(`executor.context_sources`), 마스터는 `state/fo` 캐시(하루 지나면 다운로드)."""
    acc = deps.ports["accounts"]
    return FoExecContext(
        settings=deps.settings, accounts=_fo_accounts(deps), conn=conn,
        brokers=dict(acc.fo_brokers()), market_client=acc.market_client(), master=_master(),
        notifier=deps.notifier or NullNotifier(), state=deps.state, now=deps.now,
        dry_run=bool(deps.dry_run), secrets=deps.secrets, ports=deps.ports,
        **context_sources(conn, deps.now, deps.ports),
    )  # fmt: skip


def _release_trigger(deps: Any, params: dict, why: str) -> None:
    """게이트(일시정지·휴장·장 밖)로 건너뛰는 재평가의 트리거를 '보류:' 로 닫는다 — 미처리로 남기면
    그날 그 계약의 새 트리거가 전부 막힌다(PR #64 리뷰). '보류:' 는 빈도에 세지 않는다."""
    tid = params.get("trigger_id")
    if not tid:
        return
    conn = deps.conn_factory()
    try:
        trig = xledger.get_trigger(conn, int(tid))
        if trig is not None and not trig.get("handled_at"):
            xledger.mark_trigger_handled(conn, int(tid), f"보류: {why}"[:200],
                                         handled_at=deps.now().isoformat())  # fmt: skip
    finally:
        conn.close()


# --- 잡 본문 --------------------------------------------------------------------------------


def _run_session(deps: Any) -> dict:
    conn = deps.conn_factory()
    try:
        try:
            out = fo_session.run(
                deps.settings, conn, ports=deps.ports, master=_master(), now=deps.now,
                dry_run=bool(deps.dry_run), state=deps.state, notifier=deps.notifier,
                cancel=deps.stop_event,
            )  # fmt: skip
        except PipelineError as e:  # 세션 상한(Task 6 A6) — 옛 동작은 skipped
            logger.info("파생 세션 생략: %s", e)
            return {"skipped": str(e)}
    finally:
        conn.close()
    dec = out.get("decision") or {}
    return {
        "date": out.get("date"), "skipped": out.get("skipped"), "error": out.get("error"),
        "intent": (dec.get("decision") or {}).get("intent"), "dry_run": out.get("dry_run"),
    }  # fmt: skip


def session_job(ctx: Any, job: Any) -> dict:
    """파생 아침 세션 1회(`session.run`). 계좌가 없거나 쉬는 날이면 세션 없이 끝난다."""
    if not _fo_accounts(ctx):
        return {"skipped": NO_ACCOUNTS}
    why = _day_skip(ctx)
    if why:
        logger.info("KR fo_session 건너뜀: %s", why)
        return {"skipped": why}
    return _run_session(ctx)


def monitor_job(ctx: Any, job: Any) -> dict:
    """파생 모니터(60초): 파생 정규장·모니터 창 안, 평일·비휴장일 때 `engine.tick`. 일시정지로는
    건너뛰지 않는다 — 손절·만기·축소(위험 청산)는 계속하고 진입·재평가는 엔진·실행기가 막는다."""
    if not _fo_accounts(ctx):
        return {"skipped": NO_ACCOUNTS}
    if not _fo_window(ctx):
        return {"skipped": OUT_OF_WINDOW}
    why = _day_skip(ctx, ignore_paused=True)
    if why:
        return {"skipped": why}
    conn = ctx.conn_factory()
    try:
        out = engine.tick(_fo_ctx(ctx, conn))
    finally:
        conn.close()
    keys = ("exits", "maintain", "triggers", "entries", "recalled", "halted", "error")
    summary = {
        aid: {k: (len(v) if isinstance(v, list) else v) for k, v in r.items() if k in keys}
        for aid, r in out["accounts"].items()
    }  # fmt: skip
    errs = [f"{aid}: {r['error']}" for aid, r in out["accounts"].items() if r.get("error")]
    return {"accounts": summary, **({"error": "; ".join(errs)} if errs else {})}


def reconcile_job(ctx: Any, job: Any) -> dict:
    """야간 파생 대조(옛 `reconcile_all`·`sync_fills`·`balance.refresh` 의 fo 분기): 계좌마다
    체결 동기화 → 잔고 대조(`balance.reconcile`) → 스냅샷 갱신. 계좌 하나의 실패는 다른 계좌를
    막지 않는다."""
    accounts = _fo_accounts(ctx)
    if not accounts:
        return {"skipped": NO_ACCOUNTS}
    why = _day_skip(ctx)
    if why:
        return {"skipped": why}
    brokers = dict(ctx.ports["accounts"].fo_brokers())
    today = run_date_for(MARKET, ctx.now())
    cfg = ctx.settings.fo
    out: dict[str, Any] = {"date": today.isoformat(), "accounts": {}}
    errs: list[str] = []
    conn = ctx.conn_factory()
    try:
        for a in accounts:
            broker = brokers.get(a.id)
            if broker is None:
                out["accounts"][a.id] = {"error": "broker 없음"}
                errs.append(f"{a.id}: broker 없음")
                continue
            try:
                res: dict[str, Any] = {
                    "fills": fo_fills.sync(conn, broker, a, today, cfg=cfg, now=ctx.now,
                                           notifier=ctx.notifier),  # fmt: skip
                    "reconcile": fo_balance.reconcile(conn, broker, a, today=today, now=ctx.now,
                                                      notifier=ctx.notifier),  # fmt: skip
                }
                fo_balance.refresh(conn, broker, a, now=ctx.now, cfg=cfg)
                res["snapshot"] = True
            except Exception as e:  # noqa: BLE001 — 계좌 하나의 실패가 다른 계좌 대조를 막지 않는다
                logger.exception("파생 대조 %s 실패", a.id)
                res = {"error": f"{type(e).__name__}: {e}"}
                errs.append(f"{a.id}: {res['error']}")
            out["accounts"][a.id] = res
    finally:
        conn.close()
    return {**out, **({"error": "; ".join(errs)} if errs else {})}


# --- 큐 본문 --------------------------------------------------------------------------------


def session_request(deps: Any, params: dict, progress: Any) -> dict:
    """큐 `fo_session`(사람 요청·따라잡기): 아침 세션 1회. 오늘 결정이 있으면 세션 없이 끝난다."""
    if not _fo_accounts(deps):
        return {"skipped": NO_ACCOUNTS}
    return _run_session(deps)


def reevaluate_request(deps: Any, params: dict, progress: Any) -> dict:
    """큐 `fo_reevaluate`: 일시정지·휴장·파생 장 밖이면 트리거를 '보류:' 로 닫고 건너뛴다. 아니면
    트리거 처리 여부·포지션 버전을 다시 보고 `fo_resume` 세션 1회(`reeval.run`)."""
    why = _day_skip(deps) or (None if is_fo_open(deps.now()) else FO_CLOSED)
    if why:
        _release_trigger(deps, params, why)
        return {"skipped": why}
    conn = deps.conn_factory()
    try:
        return reeval.run(_fo_ctx(deps, conn), params, cancel=deps.stop_event)
    finally:
        conn.close()


def close_request(deps: Any, params: dict, progress: Any) -> dict:
    """큐 `fo_close`(사람 요청): 실행 직전에 장 시간·계좌·포지션 상태를 다시 보고, 모드(off)·
    dry_run 은 executor 가 기록만으로 바꾼다. 사람이 요청한 청산은 위험 축소라 일시정지 중에도
    낸다(`executor.reduce` 기본 `risk_exit=True`)."""
    if not is_fo_open(deps.now()):
        return {"skipped": FO_CLOSED}
    conn = deps.conn_factory()
    try:
        ctx = _fo_ctx(deps, conn)
        account = executor.account_of(ctx, params["account_id"])
        if account is None:
            return {"skipped": f"파생 계좌 {params['account_id']} 없음"}
        pos = store.get_position(conn, int(params["position_id"]))
        if pos is None or pos.account_id != account.id:
            return {"skipped": f"포지션 {params['position_id']} 없음"}
        if pos.status in ("settled", "closed", "unknown", "awaiting_settlement"):
            return {"skipped": f"포지션 상태 {pos.status} — 자동 청산 대상 아님"}
        return executor.reduce(ctx, account, pos, intent="CLOSE", qty=None,
                               reason="수동 청산(대기열)")  # fmt: skip
    finally:
        conn.close()


def reconcile_request(deps: Any, params: dict, progress: Any) -> dict:
    """큐 `fo_reconcile`(`kr_fo_reconcile` 의 `catch_up="queue"` 대상): 잡과 같은 대조 본문."""
    return reconcile_job(deps, None)


# --- 선언 -----------------------------------------------------------------------------------

FO_SESSION = JobSpec(
    id="kr_fo_session", kind="fo_session", trigger="cron", per_market=False,
    schedule_key="fo.kr.session", interval_s=None, enabled=None, gate="none", heavy=True,
    misfire="default", catch_up="notify", start_soon_s=0, body=session_job, days="mon-fri",
)  # fmt: skip
FO_MONITOR = JobSpec(
    id="kr_fo_monitor", kind="fo_monitor", trigger="interval", per_market=False,
    schedule_key="fo.kr.monitor.interval_s", interval_s=60, enabled=None, gate="none",
    heavy=False, misfire="default", catch_up="none", start_soon_s=30, body=monitor_job,
)  # fmt: skip
FO_RECONCILE = JobSpec(
    id="kr_fo_reconcile", kind="fo_reconcile", trigger="cron", per_market=False,
    schedule_key="reconcile.kr", interval_s=None, enabled=None, gate="none", heavy=False,
    misfire="idempotent", catch_up="queue", start_soon_s=0, body=reconcile_job, days="mon-fri",
)  # fmt: skip
JOBS = (FO_SESSION, FO_MONITOR, FO_RECONCILE)

QUEUE_KINDS = (
    QueueKind("fo_session", EmptyParams, "파생 세션", "slow", 0, True, False, session_request),
    QueueKind("fo_reevaluate", FoReevaluateParams, "파생 재평가", "reeval", 10, True, False,
              reevaluate_request),  # fmt: skip
    QueueKind("fo_close", FoCloseParams, "파생 청산", "fast", 10, True, False, close_request),
    QueueKind("fo_reconcile", EmptyParams, "파생 대사", "fast", 0, False, True, reconcile_request),
)
