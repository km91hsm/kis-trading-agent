"""데몬 셸: JobSpec 에서 만든 ScheduledJob 을 APScheduler 에 올리고 run_job 하나로 실행·기록한다."""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from apscheduler import events as aps_events
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from trader.kernel.config import KST
from trader.kernel.config.reload import SettingsWatcher
from trader.kernel.daemon import heartbeat as heartbeat_mod
from trader.kernel.daemon.heartbeat import Heartbeat
from trader.kernel.daemon.jobs import ScheduledJob, build
from trader.kernel.daemon.lock import Lock, global_lock, other_root_message
from trader.kernel.ledger import runs
from trader.kernel.notify import messages
from trader.kernel.registry import hooks as hooks_mod
from trader.kernel.registry.types import Feature
from trader.kernel.session import calls

logger = logging.getLogger("trader.daemon")

HEARTBEAT_JOB_ID = "daemon_heartbeat"
HEARTBEAT_INTERVAL_S = 30


@dataclass(frozen=True)
class DaemonDeps:
    """데몬이 잡 본체에 넘기는 ctx(한 객체). 부트가 만들고, SSE 는 하트비트 파일을 읽는다."""

    settings: Any
    schedule: Any
    conn_factory: Callable[[], sqlite3.Connection]
    state: Path
    now: Callable[[], datetime]
    markets: list[str]
    gates: dict[str, Callable[..., str | None]]
    stop_event: threading.Event
    dry_run: bool
    notifier: Any = None
    secrets: Any = None
    registry: Any = None
    hooks: Any = None
    ports: dict[str, Any] = field(default_factory=dict)
    # 큐 상태(`queue_jobs.QueueState`: kind 표·heavy 락·양보 카운터). Daemon 이 없으면 만든다
    queue: Any = None
    # 등록된 기능 목록(내장 telegram_poll 이 명령 표를 만든다). Daemon 이 비어 있으면 채운다
    features: list = field(default_factory=list)


class _DropMaxInstances(logging.Filter):
    """apscheduler 의 "maximum number of running instances reached" 는 긴 잡이 도는 동안 반복되는
    정상 상황이라 버린다."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "maximum number of running instances" not in record.getMessage()


def _quiet_scheduler() -> None:
    """apscheduler 는 잡 실행마다 INFO 두 줄(하루 수만 줄)을 남긴다 → WARNING 이상만."""
    for n in ("apscheduler", "apscheduler.scheduler", "apscheduler.executors.default"):
        lg = logging.getLogger(n)
        lg.setLevel(logging.WARNING)
        if not any(isinstance(f, _DropMaxInstances) for f in lg.filters):
            lg.addFilter(_DropMaxInstances())


def _record(
    conn: sqlite3.Connection, run_id: int | None, job: ScheduledJob, status: str,
    error: str | None, result: Any,
) -> None:  # fmt: skip
    """run_id 가 없으면(interval 잡의 오류·건너뜀) 이때 시작 행을 만든다."""
    if run_id is None:
        run_id = runs.start(conn, job.kind, job.market)
    runs.finish(conn, run_id, status, error=error, result=result)


def run_job(
    conn: sqlite3.Connection, job: ScheduledJob, ctx: Any, *, stop_event: threading.Event
) -> dict:
    """잡 본체를 감싼다. cron 잡은 runs 에 시작·종료를 남기고, interval 잡은 오류일 때만 남긴다
    (분 단위 행 폭주 방지). 예외는 error 행으로 기록하고 삼킨다. body 가 `{"skipped": 사유}` 를
    돌려주거나 게이트가 사유를 주면 skipped, `{"error": …}` 면 error 로 기록한다.
    `spec.heavy` 잡은 `queue_jobs.heavy` 로 heavy 락을 기다렸다 돈다(ctx.queue 가 있을 때)."""
    spec = job.spec
    if stop_event.is_set():
        return {"skipped": "데몬 종료 중"}
    interval = spec.trigger == "interval"

    run_id = None
    try:
        gate = getattr(ctx, "gates", {}).get(spec.gate) if spec.gate else None
        if gate is not None:
            reason = gate(job)
            if reason:
                result = {"skipped": reason}
                if not interval:
                    _record(conn, None, job, "skipped", None, result)
                return result

        run_id = None if interval else runs.start(conn, job.kind, job.market)
        if spec.heavy and getattr(ctx, "queue", None) is not None:
            from trader.kernel.daemon.queue_jobs import heavy

            raw = heavy(ctx, lambda: spec.body(ctx, job), cron=True)
        else:
            raw = spec.body(ctx, job)
    except Exception as e:  # noqa: BLE001 - 잡 하나의 실패가 데몬을 죽이지 않는다
        logger.exception("잡 실패 %s", job.id)
        msg = f"{type(e).__name__}: {e}"
        _record(conn, run_id, job, "error", msg, None)
        return {"error": msg}

    result = raw if isinstance(raw, dict) else {"result": raw}
    if result.get("error"):
        _record(conn, run_id, job, "error", str(result["error"]), result)
    elif "skipped" in result:
        if run_id is not None:
            runs.finish(conn, run_id, "skipped", result=result)
    elif run_id is not None:
        runs.finish(conn, run_id, "ok", result=result)
    return result


def _trigger(job: ScheduledJob) -> Any:
    if job.spec.trigger == "cron":
        return CronTrigger(**job.trigger_kwargs)
    return IntervalTrigger(timezone=KST, **job.trigger_kwargs)


class Daemon:
    def __init__(self, features: list[Feature], deps: DaemonDeps) -> None:
        from trader.kernel.daemon.catalog import with_kernel
        from trader.kernel.daemon.queue import kinds_of
        from trader.kernel.daemon.queue_jobs import QueueState

        self.features = features
        if deps.queue is None:
            deps = replace(deps, queue=QueueState(kinds=kinds_of(with_kernel(features))))
        if not deps.features:
            deps = replace(deps, features=features)
        self.deps = deps
        self._jobs: list[ScheduledJob] = []
        self._scheduler: BackgroundScheduler | None = None
        self._lock = Lock(deps.state / heartbeat_mod.LOCK_NAME, now=deps.now)
        # 데이터 루트가 다른 데몬(개발 폴더 ↔ 설치 앱)과의 중복 기동 방지(사용자 단위 1개)
        self._global = global_lock(deps.state.parent, now=deps.now)
        self._hb = Heartbeat(deps.state, deps.now)
        self._guard = threading.Lock()
        self._started = False
        self._down = False
        self._signalled = False
        self._old_handlers: dict[int, Any] = {}
        # settings.yaml 변경 감시(레지스트리가 있을 때만 — 부트가 조립한 데몬). 잡 실행 전에 본다
        self._settings_watch = (
            SettingsWatcher(deps.registry, on_error=self._settings_error)
            if deps.registry is not None
            else None
        )

    def start(self, *, install_signals: bool = True) -> bool:
        """잡 정의 검증 → 잠금 → bootstrap 훅·큐 복구·stale 정리 → 놓친 cron 따라잡기 →
        스케줄러 등록(기능 잡 + 큐 잡 4개)·기동 → 하트비트. 잠금을 못 잡으면 False."""
        from trader.kernel.daemon import queue_jobs as qj
        from trader.kernel.daemon.catalog import with_kernel

        deps = self.deps
        self._jobs = build(
            with_kernel(self.features),
            schedule=deps.schedule, settings=deps.settings, markets=deps.markets, ctx=deps,
        )  # fmt: skip
        if not self._lock.acquire():
            return False
        if not self._global.acquire():
            owner = self._global.holder_meta().get("root") or "알 수 없음"
            logger.error(other_root_message(owner))
            self._lock.release()
            return False
        sched = BackgroundScheduler(timezone=KST)
        hb_started = False
        try:
            # 잠금을 잡은 뒤에만: 둘째 데몬이 실행 중인 데몬의 running 행을 닫으면 안 된다
            qj.bootstrap_queue(deps)
            # 만료된 queued 를 먼저 정리한 뒤 훅이 enqueue 해야 중복으로 빠지지 않는다
            hooks_mod.run(deps.hooks, "daemon.bootstrap", deps=deps)
            conn = deps.conn_factory()
            try:  # 재기동 전에 남은 running 세션 행(1시간 초과)을 error 로 닫는다
                calls.close_stale(conn, now=deps.now())
            finally:
                conn.close()
            deps.queue.heartbeat = self._hb
            try:
                qj.catch_up_missed(deps, self._jobs)
            except Exception as e:  # noqa: BLE001 - 따라잡기 실패가 기동을 막지 않는다
                logger.warning("기동 따라잡기 실패: %s", e)
            _quiet_scheduler()
            for job in self._jobs:
                extra: dict[str, Any] = {}
                if job.spec.trigger == "interval" and job.spec.start_soon_s > 0:
                    extra["next_run_time"] = deps.now() + timedelta(seconds=job.spec.start_soon_s)
                sched.add_job(
                    self._tick, _trigger(job), args=[job], id=job.id, name=job.id,
                    max_instances=1, coalesce=True,
                    misfire_grace_time=qj.misfire_grace_for(job.spec), **extra,
                )  # fmt: skip
            by_id = {j.id: j for j in self._jobs}
            sched.add_listener(  # 설정 재적재 뒤의 deps 를 쓰도록 호출 때 self.deps 를 읽는다
                lambda ev: qj.on_scheduler_event(self.deps, self._hb, by_id, ev),
                aps_events.EVENT_JOB_MISSED | aps_events.EVENT_JOB_MAX_INSTANCES,
            )
            sched.add_job(
                self._hb.beat, IntervalTrigger(seconds=HEARTBEAT_INTERVAL_S, timezone=KST),
                id=HEARTBEAT_JOB_ID, name=HEARTBEAT_JOB_ID, max_instances=1, coalesce=True,
            )  # fmt: skip
            self._hb.start(pid=os.getpid(), jobs=self._jobs)
            hb_started = True
            sched.start()
            self._scheduler = sched
            self._started = True
            for job in self._jobs:
                self._sync_next(job.id)
            if install_signals:
                self._install_signals()
        except BaseException:
            if sched.running:
                sched.shutdown(wait=False)
            self._scheduler = None
            self._started = False
            if hb_started:  # 시작 전 stop 은 직전 하트비트 파일을 빈 상태로 덮으므로 건너뛴다
                self._hb.stop()
            self._restore_signals()
            self._global.release()
            self._lock.release()
            raise
        logger.info("데몬 시작: 잡 %d개", len(self._jobs))
        return True

    def _sync_next(self, job_id: str) -> None:
        if self._scheduler is None or self._down:
            return
        aps_job = self._scheduler.get_job(job_id)
        self._hb.set_next(job_id, getattr(aps_job, "next_run_time", None))

    def _settings_error(self, msg: str) -> None:
        notifier = self.deps.notifier
        if notifier is not None:
            notifier.send(messages.settings_reload_failed(msg))

    def reload_settings(self) -> bool:
        """settings.yaml 이 바뀌었고 검증을 통과하면 `deps.settings` 를 새 설정으로 바꾼다.
        잡 표(시장·주기·켜짐)와 부트 때 조립한 포트·알림기는 그대로다(재시작 필요)."""
        watch = self._settings_watch
        if watch is None:
            return False
        try:
            new = watch.poll()
        except Exception:  # noqa: BLE001 - 감시 실패가 잡 실행을 막지 않는다
            logger.exception("settings.yaml 변경 확인 실패")
            return False
        if new is None:
            return False
        self.deps = replace(self.deps, settings=new)
        return True

    def _tick(self, job: ScheduledJob) -> None:
        """스케줄러가 부르는 진입점: 설정 변경 반영 → 하트비트 + 전용 연결로 run_job."""
        self.reload_settings()
        self._hb.job_started(job.id)
        status, error = "error", None
        conn = self.deps.conn_factory()
        try:
            out = run_job(conn, job, self.deps, stop_event=self.deps.stop_event)
            error = str(out["error"]) if out.get("error") else None
            status = "error" if error else ("skipped" if "skipped" in out else "ok")
        except BaseException as e:
            error = repr(e)
            raise
        finally:
            conn.close()
            self._hb.job_finished(job.id, status, error)
            self._sync_next(job.id)

    def _install_signals(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._old_handlers[sig] = signal.signal(sig, self._on_signal)
            except (ValueError, OSError):  # 메인 스레드가 아니면 등록 불가
                pass

    def _on_signal(self, signum: int, frame: Any) -> None:
        """첫 시그널만 `shutdown(wait=True)`. 대기 중 두 번째 시그널은 무시한다 — 대기를 끊으면
        실행 중인 잡(주문 중일 수 있음)보다 잠금이 먼저 풀려 새 데몬이 뜰 수 있다."""
        if self._signalled:
            logger.warning("시그널 %s 재수신, 이미 종료 중입니다 (실행 중인 잡을 기다림)", signum)
            return
        self._signalled = True
        logger.info("시그널 %s 수신, 실행 중인 잡을 마치고 데몬을 종료합니다", signum)
        self.shutdown(wait=True)

    def shutdown(self, wait: bool = True) -> None:
        """멱등. stop_event → 스케줄러 정지 → 하트비트 stopped_at → 잠금 해제 순."""
        with self._guard:
            if self._down:
                return
            self._down = True
        self.deps.stop_event.set()
        try:
            if self._scheduler is not None:
                try:
                    self._scheduler.shutdown(wait=wait)
                except Exception:  # noqa: BLE001 - 종료 중 오류는 무시
                    logger.exception("스케줄러 종료 실패")
            self._run_teardowns()
            if self._started:
                self._hb.stop()
        finally:
            self._restore_signals()
            self._global.release()
            self._lock.release()

    def _run_teardowns(self) -> None:
        """등록된 잡의 spec.teardown 을 spec 단위로 한 번씩 부른다(per_market 로 펼쳐져도 1회)."""
        seen: set[int] = set()
        for job in self._jobs:
            spec = job.spec
            if spec.teardown is None or id(spec) in seen:
                continue
            seen.add(id(spec))
            try:
                spec.teardown(self.deps)
            except Exception:  # noqa: BLE001 - 정리 실패가 다른 정리·잠금 해제를 막지 않는다
                logger.exception("teardown 실패 %s", spec.id)

    def _restore_signals(self) -> None:
        for sig, old in self._old_handlers.items():
            try:
                signal.signal(sig, old)
            except (ValueError, OSError):
                pass
        self._old_handlers.clear()
