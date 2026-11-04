"""system: 헬스·상태·데몬·Claude 사용량·토큰 받기·알림 기록·제어 변경 이력 (커널 자원).

`settings_router`: `PATCH /settings/usage-throttle {enabled}` — 감속 켜기·끄기
(커널이 usage 를 소유한다)."""

from __future__ import annotations

import json
import threading
import time
from contextlib import closing
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.heartbeat import read_status
from trader.kernel.ledger.control import delete_flag, get_flag, log_change, set_flag
from trader.kernel.notify import commands
from trader.kernel.notify.commands import CommandContext
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.session import calls, usage
from trader.kernel.web import health
from trader.kernel.web.auth import (
    actor_of,
    require_direct_local,
    require_same_origin,
    require_token,
)
from trader.kernel.web.routers import jobs

router = APIRouter(prefix="/system", tags=["system"])
settings_router = APIRouter(tags=["settings"])
WRITE = [Depends(require_token), Depends(require_same_origin)]

USAGE_NOTE = (
    "세션마다 claude 가 알려 준 구독 사용률(또는 대화형 상태줄 관측) 중 최신값. "
    "정확한 값은 claude 에서 /usage"
)
USAGE_ENSURE_MIN_INTERVAL_S = 60.0  # 페이지 접속 확인 요청 최소 간격(새로고침이 몰려도 haiku 1회)
_USAGE_ENSURE_LAST = [0.0]
_USAGE_ENSURE_LOCK = threading.Lock()
PROBE_KIND = "usage_probe"  # 커널 내장 kind (`daemon.builtin_jobs.USAGE_PROBE_KIND`)


def _deps(request: Request) -> Any:
    return request.app.state.deps


@router.get("/health")
def health_view(request: Request) -> dict:
    return health.evaluate(_deps(request))


@router.get("/status")
def status_view(request: Request) -> dict:
    """텔레그램 `/status` 와 같은 본문 + 일시정지 여부·control 키 이름(값은 돌려주지 않음)."""
    deps = _deps(request)
    with closing(deps.conn_factory()) as conn:
        ctx = CommandContext(
            conn=conn, settings=deps.settings, state=deps.state, now=deps.now,
            notifier=NullNotifier(), features=list(deps.features), ports=dict(deps.ports),
        )  # fmt: skip
        text = commands.status_message(ctx)
        port = deps.ports.get("accounts")
        paused = bool(port.paused(conn)) if port is not None else None
        flags = [r["key"] for r in conn.execute("SELECT key FROM control ORDER BY key")]
    return {"text": text, "paused": paused, "flags": flags}


@router.get("/daemon")
def daemon_view(request: Request) -> dict:
    """데몬 생존(daemon.lock pid)·하트비트 + 등록 잡 표(잡별 마지막 실행·다음 예정)."""
    deps = _deps(request)
    table = jobs.schedule_table(deps, status_jobs={})
    objs = [
        SimpleNamespace(id=r["id"], kind=r["kind"], market=None, cron=None,
                        interval_s=r["interval_s"])
        for r in table
    ]  # fmt: skip
    status = read_status(deps.state, deps.now(), objs)
    by_id = {j["id"]: j for j in status["jobs"]}
    keep = ("last_start", "last_end", "last_status", "last_error")
    for r in table:
        rec = by_id.get(r["id"]) or {}
        r["next_at"] = rec.get("next_run")
        r.update({k: rec.get(k) for k in keep})
    return {**status, "jobs": table}


@router.get("/usage")
def usage_view(request: Request) -> dict:
    """Claude 사용량: 최신 관측(세션 rate_limit_event 원장 ↔ 상태줄 파일) + 감속 상태 + 이 저장소
    세션의 5시간·7일 창 집계 + 세션 종류별 오늘 상한."""
    deps = _deps(request)
    now = deps.now()
    llm = deps.settings.llm
    path = usage.usage_file(llm.usage_file)
    with closing(deps.conn_factory()) as conn:
        limits = usage.latest_observation(conn, now, path)
        windows = usage.ledger_windows(conn, now, cap=llm.daily_session_cap)
        ok_new, why_new = usage.allowance(
            conn, deps.settings, throttle="hard", label="새 세션", now=now
        )
        ok_re, why_re = usage.allowance(
            conn, deps.settings, throttle="soft", label="보유 재평가", now=now
        )
        enabled, source = usage.throttle_enabled(conn, deps.settings)
        caps = [
            {
                "kind": sk.kind,
                "used": calls.sessions_today(conn, now, kind=sk.runs_kind),
                "cap": int(getattr(llm, sk.extra_cap_key)) if sk.extra_cap_key else None,
            }
            for f in deps.features
            for sk in f.session_kinds
        ]
    t = llm.usage_throttle
    return {
        "rate_limits": limits,
        "throttle": {
            "enabled": enabled,
            "source": source,  # control(설정 화면) | yaml
            "soft_pct": t.soft_pct,
            "hard_pct": t.hard_pct,
            "state": "ok" if ok_new else ("soft" if ok_re else "hard"),
            "reason": why_new or why_re,
        },
        "usage_file_present": path is not None and path.is_file(),  # 홈 경로는 돌려주지 않는다
        "windows": windows,
        "caps_by_kind": caps,
        "note": USAGE_NOTE,
    }


@router.post("/usage/ensure-fresh", dependencies=[Depends(require_same_origin)])
def usage_ensure_fresh(request: Request) -> dict:
    """페이지 접속·새로고침 때(토큰 없음, 같은 출처만): 최신 관측이 `usage_probe.skip_if_recent_min`
    보다 오래됐고 데몬이 살아 있으면 대기열에 `usage_probe` 를 넣는다(실행은 데몬, haiku 단발 1회).
    60초에 한 번만 실제로 넣고, 이미 대기·실행 중이면 그대로 둔다."""
    deps = _deps(request)
    cfg = deps.settings.llm.usage_probe
    if not cfg.enabled:
        return {"skipped": "꺼짐"}
    now = deps.now()
    with closing(deps.conn_factory()) as conn:
        obs = usage.latest_observation(conn, now, usage.usage_file(deps.settings.llm.usage_file))
        age = (obs or {}).get("age_s")
        if age is not None and age < cfg.skip_if_recent_min * 60:
            return {"fresh": True, "age_s": age}
        if not jobs.daemon_alive(deps):
            return {"skipped": "데몬 꺼짐"}
        with _USAGE_ENSURE_LOCK:
            if time.monotonic() - _USAGE_ENSURE_LAST[0] < USAGE_ENSURE_MIN_INTERVAL_S:
                return {"throttled": True}
            _USAGE_ENSURE_LAST[0] = time.monotonic()
        try:
            rid = jobq.enqueue(conn, jobs.kinds(deps), PROBE_KIND, {}, source="web", now=now)
        except jobq.DuplicateRequest:
            return {"queued": True, "already": True}
    return {"queued": True, "id": rid}


@router.post(
    "/bootstrap-token",
    dependencies=[Depends(require_direct_local), Depends(require_same_origin)],
)
def bootstrap_token(request: Request) -> dict:
    """이 PC 에서 직접 연 화면만 쓰기 토큰을 받는다(폰·tailnet 은 직접 입력)."""
    token = _deps(request).token
    if not token:
        raise HTTPException(status_code=409, detail="WEB_TOKEN 이 설정되지 않았습니다")
    return {"token": token}


@router.get("/notices")
def notices(
    request: Request, limit: int = Query(50, ge=1, le=500), kind: str | None = None
) -> list[dict]:
    """보낸 알림 기록(최신 순). ok 는 전달 성공 여부."""
    sql, args = "SELECT * FROM notices", []
    if kind:
        sql += " WHERE kind = ?"
        args.append(kind)
    with closing(_deps(request).conn_factory()) as conn:
        rows = conn.execute(sql + " ORDER BY at DESC, id DESC LIMIT ?", [*args, limit]).fetchall()
    return [{**dict(r), "ok": bool(r["ok"])} for r in rows]


def _json(value: str | None) -> Any:
    return None if value is None else json.loads(value)


@router.get("/control-log")
def control_log(request: Request, limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    """제어 변경 이력(최신 순). old/new 는 JSON 값."""
    with closing(_deps(request).conn_factory()) as conn:
        rows = conn.execute(
            "SELECT * FROM control_log ORDER BY at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{**dict(r), "old": _json(r["old"]), "new": _json(r["new"])} for r in rows]


class ThrottleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None  # None 이면 화면 설정을 지우고 yaml 값으로


@settings_router.patch("/settings/usage-throttle", dependencies=WRITE)
def usage_throttle(request: Request, body: ThrottleBody) -> dict:
    """Claude 구독 사용률 감속 켜기·끄기(설정 화면). 끄면 사용률과 무관하게 세션을 띄운다 — 하루
    세션 상한(`llm.daily_session_cap`)은 그대로. 바뀌면 control 이력에 `web` 으로 남긴다."""
    deps = _deps(request)
    with closing(deps.conn_factory()) as conn:
        old = get_flag(conn, usage.THROTTLE_FLAG)
        if body.enabled is None:
            delete_flag(conn, usage.THROTTLE_FLAG)
        else:
            set_flag(conn, usage.THROTTLE_FLAG, body.enabled)
        if old != body.enabled:
            log_change(
                conn, channel="web", actor=actor_of(request), key=usage.THROTTLE_FLAG,
                old=old, new=body.enabled, at=deps.now().isoformat(),
            )  # fmt: skip
        enabled, source = usage.throttle_enabled(conn, deps.settings)
    return {"enabled": enabled, "source": source}
