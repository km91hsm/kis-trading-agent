"""커널 내장 잡: 텔레그램 폴링(+보내지 못한 알림 재전송)·구독 사용률 확인·원장 백업·운영 점검·
보존 정리.
커널은 기능 이름을 모른다."""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from trader.kernel.config import KST
from trader.kernel.ledger import backup as ledger_backup
from trader.kernel.notify import commands
from trader.kernel.notify.commands import CommandContext
from trader.kernel.notify.telegram import make_receiver
from trader.kernel.ops.jobs import OPS_CHECK, RETENTION
from trader.kernel.registry.types import Feature, JobSpec, QueueKind
from trader.kernel.session import quick, usage

logger = logging.getLogger("trader.daemon")


def telegram_body(deps: Any, job: Any = None) -> dict:
    """outbox 재전송(실패는 삼킴) → getUpdates 1회로 명령 처리. 수신 설정이 없으면 건너뛴다."""
    flush = getattr(deps.notifier, "flush_outbox", None)
    if flush is not None:
        try:
            flush(now=deps.now())
        except Exception as e:  # noqa: BLE001 - 재전송 실패가 명령 폴링을 막지 않는다
            logger.warning("알림 재전송 실패: %s", e)
    receiver = make_receiver(deps.secrets, enabled=deps.settings.notify.enabled)
    if receiver is None:
        return {"skipped": "텔레그램 미설정"}
    conn = deps.conn_factory()
    try:
        ctx = CommandContext(
            conn=conn, settings=deps.settings, state=deps.state, now=deps.now,
            notifier=deps.notifier, features=list(deps.features), echo=logger.info,
            ports=deps.ports,
        )  # fmt: skip
        return commands.poll_once(ctx, receiver)
    finally:
        conn.close()


TELEGRAM_POLL = JobSpec(
    id="telegram_poll", kind="telegram_poll", trigger="interval", per_market=False,
    schedule_key="telegram_poll.interval_s", interval_s=30, enabled=None, gate="none",
    heavy=False, misfire="default", catch_up="none", start_soon_s=10, body=telegram_body,
)  # fmt: skip
_USAGE_PROBE_LOCK = threading.Lock()  # 정기 잡·대기열 요청이 겹쳐 haiku 를 두 번 부르지 않게
PROBE_BACKOFF = timedelta(hours=2)


def _probe_failing(conn: sqlite3.Connection, now: datetime) -> bool:
    """최근 사용량 확인 2번이 모두 실패이고 마지막 실패가 PROBE_BACKOFF 안이면 쉰다."""
    rows = conn.execute(
        "SELECT status, created_at FROM agent_calls WHERE agent = 'usage_probe' "
        "ORDER BY id DESC LIMIT 2"
    ).fetchall()
    if len(rows) < 2 or any(r["status"] != "error" for r in rows):
        return False
    try:
        last = datetime.fromisoformat(rows[0]["created_at"])
    except (TypeError, ValueError):
        return False
    return now - last < PROBE_BACKOFF


def _usage_probe_once(deps: Any, cfg: Any, *, force: bool) -> dict:
    now = deps.now()
    conn = deps.conn_factory()
    try:
        # 원장(세션·확인)과 대화형 상태줄 파일을 합친 최신 관측 — 둘 중 하나라도 최근이면 쉰다
        obs = usage.latest_observation(conn, now, usage.usage_file(deps.settings.llm.usage_file))
        age = (obs or {}).get("age_s")
        if not force and age is not None and age < cfg.skip_if_recent_min * 60:
            return {"skipped": f"최근 관측 {int(age)}초 전"}
        ok, reason = usage.session_allowance(conn, deps.settings, kind="resume", now=now)
        if not ok:
            return {"skipped": f"감속: {reason}"}
        if not force and _probe_failing(conn, now):
            return {"skipped": f"연속 실패 — {PROBE_BACKOFF.seconds // 3600}시간 쉼"}
        out = quick.probe_usage(deps.settings, conn, now=deps.now, cancel=deps.stop_event)
        if isinstance(out, dict) and out.get("error"):
            # 확인 실패는 다음 주기에 다시 — 잡 오류로 쌓지 않는다(실패 기록은 agent_calls 에 남음)
            return {"skipped": f"확인 실패(다음 주기): {out['error']}"}
        return out
    finally:
        conn.close()


def usage_probe_body(deps: Any, job: Any = None, *, force: bool = False) -> dict:
    """구독 사용률 확인. 최근 관측이 있거나 감속 hard 이상이면 건너뛴다. force 면 최근 관측이
    있어도 확인한다(감속은 그대로 본다)."""
    cfg = deps.settings.llm.usage_probe
    if deps.stop_event.is_set() or not cfg.enabled:
        return {"skipped": "종료 중·꺼짐"}
    if not _USAGE_PROBE_LOCK.acquire(blocking=False):
        return {"skipped": "다른 확인이 도는 중"}
    try:
        return _usage_probe_once(deps, cfg, force=force)
    finally:
        _USAGE_PROBE_LOCK.release()


def ledger_backup_body(deps: Any, job: Any = None) -> dict:
    """원장 백업(매일 KST): 사본 1개 + 보존 규칙 정리. 실패는 run_job 이 기록한다."""
    out = ledger_backup.backup(state=deps.state, now=deps.now())
    removed = ledger_backup.prune(out.parent, today=deps.now().astimezone(KST).date())
    return {"file": out.name, "pruned": removed}


USAGE_PROBE = JobSpec(
    id="usage_probe", kind="usage_probe", trigger="interval", per_market=False,
    schedule_key=None, interval_s=1800, enabled=lambda s: bool(s.llm.usage_probe.enabled),
    gate="none", heavy=False, misfire="default", catch_up="none", start_soon_s=60,
    body=usage_probe_body,
)  # fmt: skip
LEDGER_BACKUP = JobSpec(
    id="ledger_backup", kind="ledger_backup", trigger="cron", per_market=False,
    schedule_key="ledger_backup", interval_s=None, enabled=None, gate="none", heavy=False,
    misfire="idempotent", catch_up="direct", start_soon_s=0, body=ledger_backup_body,
)  # fmt: skip
BUILTIN_JOBS: tuple[JobSpec, ...] = (
    TELEGRAM_POLL, USAGE_PROBE, LEDGER_BACKUP, OPS_CHECK, RETENTION,
)  # fmt: skip


class UsageProbeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False  # True = 시스템 화면 '지금 확인'(최근 관측 무시), 페이지 접속 요청은 False


def usage_probe_request(deps: Any, params: dict, progress: Any = None) -> dict:
    """큐 요청 `usage_probe` → 내장 잡과 같은 본체(`usage_probe_body`)."""
    return usage_probe_body(deps, force=bool(params.get("force")))


USAGE_PROBE_KIND = QueueKind(
    kind="usage_probe", params_model=UsageProbeParams, label="Claude 사용량 확인", lane="info",
    priority=0, trading=False, restart_requeue=False, body=usage_probe_request,
)  # fmt: skip
BUILTIN_FEATURE = Feature(
    id="kernel_builtin", tier="service", jobs=BUILTIN_JOBS, queue_kinds=(USAGE_PROBE_KIND,)
)
