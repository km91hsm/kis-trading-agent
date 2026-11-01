"""데몬 셸: JobSpec 에서 만든 ScheduledJob 을 APScheduler 에 올리고 run_job 하나로 실행·기록한다."""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from trader.kernel.config import KST
from trader.kernel.daemon import heartbeat as heartbeat_mod
from trader.kernel.daemon.heartbeat import Heartbeat
from trader.kernel.daemon.jobs import ScheduledJob, build
from trader.kernel.daemon.lock import Lock
from trader.kernel.ledger import runs
from trader.kernel.registry.types import Feature

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
    돌려주거나 게이트가 사유를 주면 skipped, `{"error": …}` 면 error 로 기록한다."""
    spec = job.spec
    if stop_event.is_set():
        return {"skipped": "데몬 종료 중"}
    interval = spec.trigger == "interval"

    gate = getattr(ctx, "gates", {}).get(spec.gate) if spec.gate else None
    if gate is not None:
        reason = gate(job)
        if reason:
            result = {"skipped": reason}
            if not interval:
                _record(conn, None, job, "skipped", None, result)
            return result

    run_id = None if interval else runs.start(conn, job.kind, job.market)
    try:
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
        self.features = features
        self.deps = deps
        self._jobs: list[ScheduledJob] = []
        self._scheduler: BackgroundScheduler | None = None
        self._lock = Lock(deps.state / heartbeat_mod.LOCK_NAME)
        self._hb = Heartbeat(deps.state, deps.now)
        self._guard = threading.Lock()
        self._started = False
        self._down = False
        self._signalled = False
        self._old_handlers: dict[int, Any] = {}

    def start(self, *, install_signals: bool = True) -> bool:
        """잡 정의 검증 → 잠금 → 스케줄러 등록·기동 → 하트비트. 잠금을 못 잡으면 False."""
        deps = self.deps
        self._jobs = build(
            self.features, schedule=deps.schedule, settings=deps.settings,
            markets=deps.markets, ctx=deps,
        )  # fmt: skip
        if not self._lock.acquire():
            return False
        try:
            _quiet_scheduler()
            sched = BackgroundScheduler(timezone=KST)
            for job in self._jobs:
                sched.add_job(
                    self._tick, _trigger(job), args=[job], id=job.id, name=job.id,
                    max_instances=1, coalesce=True,
                )  # fmt: skip
            sched.add_job(
                self._hb.beat, IntervalTrigger(seconds=HEARTBEAT_INTERVAL_S, timezone=KST),
                id=HEARTBEAT_JOB_ID, name=HEARTBEAT_JOB_ID, max_instances=1, coalesce=True,
            )  # fmt: skip
            self._hb.start(pid=os.getpid(), jobs=self._jobs)
            sched.start()
            self._scheduler = sched
            self._started = True
            for job in self._jobs:
                self._sync_next(job.id)
            if install_signals:
                self._install_signals()
        except BaseException:
            self._lock.release()
            raise
        logger.info("데몬 시작: 잡 %d개", len(self._jobs))
        return True

    def _sync_next(self, job_id: str) -> None:
        if self._scheduler is None or self._down:
            return
        aps_job = self._scheduler.get_job(job_id)
        self._hb.set_next(job_id, getattr(aps_job, "next_run_time", None))

    def _tick(self, job: ScheduledJob) -> None:
        """스케줄러가 부르는 진입점: 하트비트 + 전용 연결로 run_job."""
        self._hb.job_started(job.id)
        status, error = "error", None
        conn = self.deps.conn_factory()
        try:
            out = run_job(conn, job, self.deps, stop_event=self.deps.stop_event)
            error = str(out["error"]) if out.get("error") else None
            status = "error" if error else ("skipped" if "skipped" in out else "ok")
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
            if self._started:
                self._hb.stop()
        finally:
            for sig, old in self._old_handlers.items():
                try:
                    signal.signal(sig, old)
                except (ValueError, OSError):
                    pass
            self._old_handlers.clear()
            self._lock.release()
