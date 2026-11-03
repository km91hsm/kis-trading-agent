"""잡 큐 소비(4레인 틱)·heavy 락·재기동 복구·놓친 cron 따라잡기. 커널은 kind 이름을 모른다.

레인·우선순위·재기동 재등록·trading 여부는 기능이 선언한 `QueueKind` 가 정한다.

- 레인: `job_queue`(slow, heavy 락을 비차단으로 잡고 cron 이 기다리면 양보) · `job_queue_fast` ·
  `job_queue_reeval` · `job_queue_info`. 각 틱은 자기 레인 kind 만 claim 한다.
- heavy 락: 무거운 잡은 한 번에 하나. heavy cron(`JobSpec.heavy`)은 `heavy()` 로 락을 기다리며
  `heavy_wanted` 를 세우고, 느린 레인 요청은 progress 경계에서 락을 내놓는다(`_yield_heavy`).
- 자동 요청(`AUTO_SOURCES`)의 trading kind 는 꺼낼 때 시장 게이트·장 마감을 다시 본다.
- 상태(`QueueState`)는 모듈 전역이 아니라 `DaemonDeps.queue` 에 둔다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from apscheduler.triggers.cron import CronTrigger

from trader.kernel.config.market_hours import has_session, session_over
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.jobs import ScheduledJob
from trader.kernel.daemon.runner import run_job
from trader.kernel.notify import messages
from trader.kernel.registry.types import Feature, JobSpec
from trader.kernel.session.run_store import run_date_for
from trader.kernel.session.runner import ThrottleBlocked

logger = logging.getLogger("trader.daemon")

HEAVY_POLL_S = 30.0  # heavy 락 대기 폴링 간격
HEAVY_WAIT_NOTIFY_S = 600.0  # cron 이 이만큼 기다리면 알림 1건
QUEUED_MAX_AGE = timedelta(hours=6)  # 기동 때 이보다 오래 대기한 요청은 만료
AUTO_SOURCES = frozenset({"daemon", "news", "notice", "restart"})  # 꺼낼 때 게이트 재검사
RESTART_SOURCE = "restart"

SCAN_MISFIRE_GRACE_S = 3600  # 절전 등으로 늦어도 1시간 안이면 그냥 돈다(하루를 잃는 것보다 낫다)
DEFAULT_MISFIRE_GRACE_S = 120
IDEMPOTENT_MISFIRE_GRACE_S = 6 * 3600  # 멱등 cron 은 몇 시간 늦어도 그냥 돈다
CATCH_UP_LOOKBACK = timedelta(hours=20)  # 기동 때 '오늘 놓친' 것으로 보는 범위
PROGRESS_EVERY_S = 1.0  # 진행 문구는 이 간격으로만 DB 에 적는다

# 큐 잡 id → 그 틱이 꺼내는 레인
QUEUE_LANES: dict[str, frozenset[str]] = {
    "job_queue": frozenset({"slow"}),
    "job_queue_fast": frozenset({"fast"}),
    "job_queue_reeval": frozenset({"reeval"}),
    "job_queue_info": frozenset({"info"}),
}
QUEUE_JOB_IDS = frozenset(QUEUE_LANES)


class HeavyWanted:
    """heavy 락을 기다리는 cron 잡 수. Event 하나로는 cron 둘이 기다릴 때 먼저 끝난 쪽이 지워
    남은 쪽의 양보 요청이 사라진다 — 수를 세어 0 이 될 때만 꺼진다."""

    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def enter(self) -> None:
        with self._lock:
            self._n += 1

    def leave(self) -> None:
        with self._lock:
            self._n = max(0, self._n - 1)

    def is_set(self) -> bool:
        with self._lock:
            return self._n > 0


@dataclass
class QueueState:
    """데몬 수명 동안의 큐 상태. kinds 는 기능이 선언한 QueueKind 표(`queue.kinds_of`).
    heavy_owner 는 heavy 락을 쥔 스레드 id — 양보는 쥔 스레드만 한다(빠른 레인의 progress 가
    남의 락을 풀지 않게). heartbeat 는 Daemon 이 붙인다(없으면 하트비트 기록 생략)."""

    kinds: jobq.Kinds = field(default_factory=dict)
    heavy_lock: threading.Lock = field(default_factory=threading.Lock)
    heavy_wanted: HeavyWanted = field(default_factory=HeavyWanted)
    heavy_owner: int | None = None
    heartbeat: Any = None


def _send(deps: Any, text: str) -> None:
    notifier = getattr(deps, "notifier", None)
    if notifier is None:
        return
    try:
        notifier.send(text)
    except Exception:  # noqa: BLE001 - 알림 실패가 잡을 막지 않는다
        logger.debug("알림 실패", exc_info=True)


# --- heavy 락 -------------------------------------------------------------------------


def heavy(deps: Any, fn: Callable[[], Any], *, cron: bool = True) -> Any:
    """무거운 잡은 한 번에 하나. cron 은 기다리되 `heavy_wanted` 를 세워 큐 잡이 양보하게 하고,
    HEAVY_WAIT_NOTIFY_S 넘게 기다리면 알림 1건을 보낸다. 기다리는 중 종료 신호면 건너뛴다."""
    q: QueueState = deps.queue
    if cron:
        q.heavy_wanted.enter()
    waited = 0.0
    notified = False
    try:
        while not q.heavy_lock.acquire(timeout=HEAVY_POLL_S):
            waited += HEAVY_POLL_S
            if deps.stop_event.is_set():
                return {"skipped": "종료 중"}
            if waited >= HEAVY_WAIT_NOTIFY_S and not notified:
                notified = True
                logger.warning("cron 잡이 heavy 락을 %.0f분 기다리는 중", waited / 60)
                _send(
                    deps,
                    messages.event(
                        "잡 대기",
                        f"예정된 잡이 다른 무거운 잡 때문에 {waited / 60:.0f}분 기다리고 있어요",
                    ),
                )
    finally:
        if cron:
            q.heavy_wanted.leave()
    q.heavy_owner = threading.get_ident()
    try:
        return fn()
    finally:
        q.heavy_owner = None
        q.heavy_lock.release()


def _yield_heavy(deps: Any) -> None:
    """느린 레인 요청이 progress 경계에서 부른다: cron 이 기다리면 heavy 락을 내놓고 cron 이 끝난
    뒤 다시 잡는다. 이 스레드가 락을 쥐고 있지 않으면 아무것도 하지 않는다."""
    q: QueueState = deps.queue
    me = threading.get_ident()
    if not q.heavy_wanted.is_set() or q.heavy_owner != me:
        return
    logger.info("큐 요청이 예정 잡에 양보 (heavy 락 반납)")
    q.heavy_owner = None
    q.heavy_lock.release()
    try:
        while q.heavy_wanted.is_set() and not deps.stop_event.is_set():
            deps.stop_event.wait(0.05)
    finally:
        q.heavy_lock.acquire()
        q.heavy_owner = me
    logger.info("큐 요청 재개")


# --- 큐 틱 ----------------------------------------------------------------------------


def lane_kinds(kinds: jobq.Kinds, lanes: frozenset[str]) -> frozenset[str]:
    """레인 집합에 속한 kind 이름."""
    return frozenset(k for k, q in kinds.items() if q.lane in lanes)


def is_auto_source(source: str | None) -> bool:
    """자동 출처인가. `notice:{env}` 처럼 ':' 뒤에 붙은 꼬리는 떼고 앞부분만 `AUTO_SOURCES` 와
    맞춘다(':' 가 없으면 그대로 정확히 맞춘다)."""
    return bool(source) and str(source).split(":", 1)[0] in AUTO_SOURCES


def auto_gate(deps: Any, req: dict) -> str | None:
    """자동 요청(데몬 따라잡기·뉴스·체결통보·재기동 재등록)의 trading kind 는 넣은 뒤 꺼내기
    전에 장이 끝나거나 일시정지·휴장일일 수 있다 — 꺼내는 시점에 시장 게이트와 장 마감을 다시
    본다. 사람이 누른 요청(web·telegram·cli)과 trading 이 아닌 kind 는 그대로 돈다."""
    spec = deps.queue.kinds.get(req["kind"])
    if spec is None or not spec.trading or not is_auto_source(req.get("source")):
        return None
    market = (req.get("params") or {}).get("market")
    gate = (deps.gates or {}).get("market")
    if gate is not None:
        why = gate(SimpleNamespace(market=market, spec=None, id=req["kind"]))
        if why:
            return why
    if market in MARKET_TZ and session_over(market, deps.now()):
        return "장 마감"
    return None


def queued_job_body(deps: Any, req: dict, progress: Callable[[str], None]) -> dict:
    """큐 요청 → 기능 본체 `QueueKind.body(deps, params, progress)`. 자동 trading 요청은 먼저
    `auto_gate` 를 본다(사유가 있으면 본체 없이 `{"skipped": 사유}`)."""
    kinds = deps.queue.kinds
    spec = kinds.get(req["kind"])
    if spec is None:
        raise ValueError(f"알 수 없는 작업: {req['kind']}")
    why = auto_gate(deps, req)
    if why:
        return {"skipped": why}
    raw = spec.body(deps, req["params"], progress)
    return raw if isinstance(raw, dict) else {"result": raw}


def _progress_writer(deps: Any, req_id: int) -> Callable[[str], None]:
    """진행 문구를 PROGRESS_EVERY_S 에 한 번만 DB 에 적는다. 종료 신호면 `JobCancelled`, cron 이
    heavy 락을 기다리면 양보한다."""
    state = {"n": 0, "last": None}

    def write(text: str) -> None:
        state["n"] += 1
        if deps.stop_event.is_set():
            raise jobq.JobCancelled("종료 중")
        _yield_heavy(deps)
        mono = time.monotonic()
        if state["last"] is not None and mono - state["last"] < PROGRESS_EVERY_S:
            return
        state["last"] = mono
        conn = deps.conn_factory()
        try:
            jobq.set_progress(conn, req_id, f"[{state['n']}] {text}")
        finally:
            conn.close()

    return write


def _request_job(name: str, req: dict, body: Callable[..., Any]) -> ScheduledJob:
    """큐 요청 한 건을 run_job 이 기록할 수 있는 모양으로 감싼다. runs 행은 cron 과 같은
    (kind, market) 이라 놓친 잡 집계가 큐로 따라잡은 실행도 '완료' 로 센다."""
    market = (req.get("params") or {}).get("market")
    spec = JobSpec(
        id=name, kind=req["kind"], trigger="cron", per_market=False, schedule_key=None,
        interval_s=None, enabled=None, gate="none", heavy=False, misfire="default",
        catch_up="none", start_soon_s=0, body=body,
    )  # fmt: skip
    return ScheduledJob(name, spec, str(market) if market else None, {})


def queue_tick(deps: Any, *, lanes: frozenset[str]) -> dict:
    """레인의 큐에서 요청 1건을 꺼내 실행한다. slow 레인은 heavy 락을 비차단으로 잡고(잠겨
    있으면 claim 하지 않음), cron 이 락을 기다리면 새 요청을 꺼내지 않는다."""
    if deps.stop_event.is_set():
        return {"skipped": "종료 중"}
    q: QueueState = deps.queue
    if "slow" not in lanes:
        return _tick_locked(deps, lanes)
    if q.heavy_wanted.is_set():
        return {"skipped": "예정 잡이 기다리는 중"}
    if not q.heavy_lock.acquire(blocking=False):
        return {"skipped": "다른 무거운 잡 실행 중"}
    q.heavy_owner = threading.get_ident()
    try:
        return _tick_locked(deps, lanes)
    finally:
        q.heavy_owner = None
        q.heavy_lock.release()


def _tick_locked(deps: Any, lanes: frozenset[str]) -> dict:
    q: QueueState = deps.queue
    names = lane_kinds(q.kinds, lanes)
    conn = deps.conn_factory()
    try:
        req = jobq.claim_next(conn, now=deps.now(), kinds=names)
    finally:
        conn.close()
    if req is None:
        return {"skipped": "대기 없음"}
    req_id, kind = int(req["id"]), req["kind"]
    name = jobq.job_id_for(q.kinds, kind, req["params"])
    logger.info("큐 요청 #%d %s 시작 (%s)", req_id, kind, req["source"])
    done = {"v": False}

    def _finish(status: str, result: dict | None = None, error: str | None = None) -> None:
        # done 은 기록이 성공한 뒤에만 — 실패하면 아래 finally 의 `_close_unrun` 이 다시 닫는다
        # (running 으로 남으면 EmptyParams kind 는 재기동 전까지 전부 DuplicateRequest)
        c = deps.conn_factory()
        try:
            jobq.finish(c, req_id, status, result=result, error=error, now=deps.now())  # type: ignore[arg-type]
        finally:
            c.close()
        done["v"] = True

    def wrapped(ctx: Any, job: ScheduledJob) -> Any:
        try:
            result = queued_job_body(deps, req, _progress_writer(deps, req_id))
        except jobq.JobCancelled as e:
            _finish("skipped", error=str(e))
            return {"skipped": str(e)}
        except ThrottleBlocked as e:  # 사용량 감속은 실패가 아니라 생략
            _finish("skipped", error=f"사용량 감속 — {e}")
            return {"skipped": f"사용량 감속 — {e}"}
        except Exception as e:
            _finish("error", error=f"{type(e).__name__}: {e}")
            raise
        err = result.get("error")
        status = "error" if err else ("skipped" if "skipped" in result else "ok")
        _finish(status, result=result, error=str(err) if err else None)
        return result

    hb = q.heartbeat
    if hb is not None:
        hb.job_started(name)
    status, error = "error", None
    out: dict | None = None
    try:
        conn = deps.conn_factory()  # 실패해도 아래 finally 가 꺼낸 요청을 닫는다
        try:
            job = _request_job(name, req, wrapped)
            out = run_job(conn, job, deps, stop_event=deps.stop_event)
        finally:
            conn.close()
        error = str(out["error"]) if out.get("error") else None
        status = "error" if error else ("skipped" if "skipped" in out else "ok")
    except BaseException as e:
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        if not done["v"]:  # 본체가 돌지 않았다 — 요청을 running 으로 남기지 않는다
            _close_unrun(deps, _finish, out, error)
        if hb is not None:
            hb.job_finished(name, status, error)
    return {"request": req_id, "kind": kind}


def _close_unrun(
    deps: Any, finish: Callable[..., None], out: dict | None, error: str | None
) -> None:
    """본체 전에 끝난 요청의 최종 상태: run_job 의 오류면 error, 종료 신호로 멈췄으면 skipped,
    그 밖(연결 실패 등)은 error. 닫기 자체가 실패해도 예외를 더 올리지 않는다(다음 기동이 정리)."""
    try:
        if out is not None and out.get("error"):
            finish("error", error=str(out["error"]))
        elif deps.stop_event.is_set() or (out is not None and "skipped" in out):
            finish("skipped", error="데몬 종료 중")
        else:
            finish("error", error=error or "본체 실행 전 중단")
    except Exception:  # noqa: BLE001
        logger.exception("큐 요청 종료 기록 실패")


def _queue_body(lanes: frozenset[str]) -> Callable[[Any, Any], dict]:
    def body(ctx: Any, job: Any) -> dict:
        return queue_tick(ctx, lanes=lanes)

    return body


QUEUE_JOBS: tuple[JobSpec, ...] = tuple(
    JobSpec(
        id=ident, kind=ident, trigger="interval", per_market=False,
        schedule_key="job_queue.interval_s", interval_s=10, enabled=None, gate="none",
        heavy=False, misfire="default", catch_up="none", start_soon_s=0, body=_queue_body(lanes),
    )  # fmt: skip
    for ident, lanes in QUEUE_LANES.items()
)
QUEUE_FEATURE = Feature(id="kernel_job_queue", tier="service", jobs=QUEUE_JOBS)


def enqueue_from_job(
    deps: Any, kind: str, params: dict, *, source: str, priority: int | None = None
) -> int | None:
    """잡·리스너가 큐에 요청을 넣는다. 같은 요청이 대기·실행 중이면 None."""
    conn = deps.conn_factory()
    try:
        return jobq.enqueue(
            conn, deps.queue.kinds, kind, params, source=source, now=deps.now(), priority=priority
        )
    except jobq.DuplicateRequest:
        return None
    finally:
        conn.close()


# --- 재기동 복구 ------------------------------------------------------------------------


def bootstrap_queue(deps: Any) -> dict:
    """기동 때: 이전 running → error('데몬 재시작'), 오래된 queued → 만료, `restart_requeue` kind
    만 같은 params 로 다시 넣는다(source 'restart'). 끊긴 요청은 알림 1건으로 모두 알린다 —
    trading kind(재평가 등)는 끊긴 시점에 이미 주문을 냈을 수 있어 다시 넣지 않는다."""
    q: QueueState = deps.queue
    now = deps.now()
    conn = deps.conn_factory()
    requeued: list[str] = []
    try:
        interrupted = [
            jobq._hydrate(r)
            for r in conn.execute(
                "SELECT * FROM job_requests WHERE status='running' ORDER BY id"
            ).fetchall()
        ]
        stale = jobq.mark_stale_running(conn, now=now, reason="데몬 재시작")
        expired = jobq.expire_queued(conn, older_than=now - QUEUED_MAX_AGE)
        for r in interrupted:
            spec = q.kinds.get(r["kind"])
            if spec is None or not spec.restart_requeue:
                continue
            try:
                jobq.enqueue(
                    conn, q.kinds, r["kind"], r["params"], source=RESTART_SOURCE, now=now
                )
                requeued.append(f"{r['kind']}#{r['id']}")
            except ValueError:  # DuplicateRequest 포함 — 이미 대기 중이거나 params 가 바뀜
                pass
    finally:
        conn.close()
    if interrupted:
        _send(
            deps,
            messages.event(
                "작업 중단",
                f"데몬 재시작으로 실행 중이던 요청 {len(interrupted)}건이 끊겼어요",
                ", ".join(f"{r['kind']}#{r['id']}" for r in interrupted),
                f"다시 등록: {', '.join(requeued)}" if requeued else "",
            ),
        )
    if stale or expired or requeued:
        logger.info(
            "큐 정리: running→error %d, 만료 %d, 재등록 %s", stale, expired, requeued or "-"
        )
    return {"stale": stale, "expired": expired, "requeued": requeued}


# --- 놓친 cron ------------------------------------------------------------------------


def misfire_grace_for(spec: JobSpec) -> int:
    """스케줄러 misfire grace(초): scan 1시간, 멱등 6시간, 나머지 2분."""
    if spec.misfire == "scan":
        return SCAN_MISFIRE_GRACE_S
    if spec.misfire == "idempotent":
        return IDEMPOTENT_MISFIRE_GRACE_S
    return DEFAULT_MISFIRE_GRACE_S


def _market_skip(deps: Any, job: ScheduledJob) -> str | None:
    """시장 잡이 오늘 쉬는 사유(일시정지·주말·휴장). 게이트가 없으면 정규장 유무만 본다."""
    gate = (deps.gates or {}).get("market")
    if gate is not None:
        return gate(job)
    market = job.market
    if market in MARKET_TZ and not has_session(market, run_date_for(market, deps.now())):
        return "장 없음"
    return None


def on_cron_missed(deps: Any, job: ScheduledJob, *, notify: bool = True) -> dict:
    """cron 잡을 놓쳤다(grace 초과·데몬이 꺼져 있었음). 시장이 쉬는 날이면 조용히 끝낸다.
    `catch_up`: queue 는 큐에 1회(중복 무시, trading kind 는 장이 끝났으면 알림만), direct 는
    그 자리에서 실행, notify 는 알림만, none 은 아무것도 하지 않는다."""
    out: dict[str, Any] = {"job": job.id, "queued": False}
    mode = job.spec.catch_up
    if mode == "none":
        return out
    if job.market:
        why = _market_skip(deps, job)
        if why:
            out["skipped"] = why
            return out
    if notify:
        _send(deps, messages.event("잡 놓침", f"{job.id} — 예정 시각을 지나 실행되지 않았어요"))
    if mode == "direct":
        conn = deps.conn_factory()
        try:
            run_job(conn, job, deps, stop_event=deps.stop_event)
            out["ran"] = True
        except Exception as e:  # noqa: BLE001 - 리스너·기동 따라잡기를 죽이지 않는다
            logger.warning("놓친 %s 바로 실행 실패: %s", job.id, e)
        finally:
            conn.close()
        return out
    if mode != "queue":
        return out
    kind = deps.queue.kinds.get(job.kind)
    if kind is None:
        logger.warning("놓친 %s: 큐 kind %s 가 없어 따라잡지 않음", job.id, job.kind)
        return out
    if kind.trading and job.market in MARKET_TZ and session_over(job.market, deps.now()):
        out["late"] = "장 마감 — 따라잡지 않음"
        return out
    params = {"market": job.market} if job.market else {}
    try:
        out["queued"] = enqueue_from_job(deps, job.kind, params, source="daemon") is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("놓친 %s 따라잡기 등록 실패: %s", job.id, e)
    return out


def _last_fire(job: ScheduledJob, now: datetime) -> datetime | None:
    """now 이전 가장 최근 예정 시각(CATCH_UP_LOOKBACK 안). cron 이 아니면 None."""
    if job.spec.trigger != "cron":
        return None
    trig = CronTrigger(**job.trigger_kwargs)
    start = now - CATCH_UP_LOOKBACK
    last, fire = None, trig.get_next_fire_time(None, start)
    while fire is not None and fire <= now:
        last = fire
        fire = trig.get_next_fire_time(fire, fire + timedelta(seconds=1))
    return last


def catch_up_missed(deps: Any, jobs: Iterable[ScheduledJob]) -> dict:
    """기동 때: 데몬이 꺼져 있던 동안 오늘 예정 시각이 지난 cron 잡 중 끝까지 돈(`ok`) runs 기록이
    없는 것을 놓친 것으로 본다(APScheduler 는 새로 등록한 잡의 지난 시각에 MISSED 를 내지 않는다).
    지난 운영일 예정분은 끌어오지 않는다. 알림은 한 건으로 묶는다."""
    now = deps.now()
    jobs = [j for j in jobs if j.spec.trigger == "cron" and j.spec.catch_up != "none"]
    missed: list[ScheduledJob] = []
    conn = deps.conn_factory()
    try:
        for job in jobs:
            fire = _last_fire(job, now)
            if fire is None:
                continue
            if job.market in MARKET_TZ:
                if run_date_for(job.market, fire) != run_date_for(job.market, now):
                    continue
            else:
                tz = job.trigger_kwargs.get("timezone")
                if fire.astimezone(tz).date() != now.astimezone(tz).date():
                    continue
            ran = conn.execute(
                "SELECT 1 FROM runs WHERE kind=? AND market IS ? AND started_at >= ? "
                "AND status='ok' LIMIT 1",
                (job.kind, job.market, fire.astimezone(UTC).isoformat()),
            ).fetchone()
            if ran is None:
                missed.append(job)
    finally:
        conn.close()
    real: list[str] = []
    queued: list[str] = []
    for job in missed:
        r = on_cron_missed(deps, job, notify=False)
        if "skipped" in r:
            continue
        real.append(job.id)
        if r.get("queued") or r.get("ran"):
            queued.append(job.id)
    if real:
        _send(
            deps,
            messages.event(
                "잡 놓침",
                f"데몬이 꺼져 있던 동안 {len(real)}개 — {', '.join(real)}",
                f"따라잡기 등록: {', '.join(queued)}" if queued else "",
            ),
        )
    return {"missed": real, "queued": queued}


def on_scheduler_event(deps: Any, hb: Any, by_id: dict[str, ScheduledJob], event: Any) -> None:
    """APScheduler MISSED·MAX_INSTANCES 리스너. 큐 틱의 MAX_INSTANCES 는 긴 요청을 도는 동안
    매 틱 나는 정상 상황이라 놓침으로 적지 않는다. cron MISSED 는 `on_cron_missed`."""
    from apscheduler import events as ev

    if event.code == ev.EVENT_JOB_MAX_INSTANCES and event.job_id in QUEUE_JOB_IDS:
        return
    if hb is not None:
        try:
            hb.job_finished(event.job_id, "missed")
        except Exception:  # noqa: BLE001 - 하트비트는 보조 정보
            logger.debug("하트비트 놓침 기록 실패", exc_info=True)
    job = by_id.get(event.job_id)
    if event.code == ev.EVENT_JOB_MISSED and job is not None and job.spec.trigger == "cron":
        try:
            on_cron_missed(deps, job)
        except Exception:  # noqa: BLE001 - 리스너는 죽지 않는다
            logger.exception("놓친 잡 처리 실패 %s", event.job_id)
