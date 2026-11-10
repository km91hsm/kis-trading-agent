"""jobs: 잡 큐 자원. 조회는 토큰 없음, 요청·취소는 토큰 + 같은 출처(조작 이력 `job:<kind>`).

kind 표는 발견된 기능 + 커널 잡(`catalog.with_kernel`)의 `QueueKind` 다(데몬·CLI 와 같은 목록).
실행 주체는 데몬의 큐 틱뿐이라 데몬이 살아 있지 않으면 요청을 받지 않는다(409).
`trading` kind 가 실전 계좌(`params.account_id`)를 가리키면 본문 `confirm` 에 계좌 id 가 필요하다.
대상 계좌가 없는 `trading` kind 는 실전 계좌가 하나라도 있으면 `confirm` 이 실전 계좌 id 중
하나여야 하고, 계좌 포트가 없으면 거절한다(fail-safe, M11 Ruling T3-R4).
"""

from __future__ import annotations

from contextlib import closing
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.daemon import activity
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.catalog import with_kernel
from trader.kernel.daemon.heartbeat import read_status
from trader.kernel.web.auth import (
    actor_of,
    require_confirm,
    require_same_origin,
    require_token,
)

DAEMON_DOWN = "데몬이 꺼져 있어요 — 요청은 데몬이 실행해요 (uv run trader daemon)"
CANCEL_ONLY_QUEUED = "대기 중인 요청만 취소할 수 있어요"

router = APIRouter(tags=["jobs"])
WRITE = [Depends(require_token), Depends(require_same_origin)]


class JobBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    params: dict = Field(default_factory=dict)
    confirm: str | None = None


def kinds(deps: Any) -> jobq.Kinds:
    return jobq.kinds_of(with_kernel(deps.features))


def daemon_alive(deps: Any) -> bool:
    return bool(read_status(deps.state, deps.now(), [])["alive"])


def _all_accounts(port: Any) -> list[Any]:
    """현물 `accounts()` + 파생 `fo_accounts()`(있으면)."""
    fo = getattr(port, "fo_accounts", None)
    return [*port.accounts(), *(fo() if callable(fo) else [])]


def account_env(deps: Any, account_id: str) -> str:
    """`ports["accounts"]`(커널 Protocol `AccountsPort`, 현물 + 파생)에서 계좌 env. 포트가 없거나
    모르는 계좌면 'real' — 실전으로 보고 confirm 을 요구한다(fail-safe)."""
    port = (deps.ports or {}).get("accounts")
    if port is None:
        return "real"
    for acct in _all_accounts(port):
        if getattr(acct, "id", None) == account_id:
            return str(getattr(acct, "env", None) or "real")
    return "real"


def require_confirm_any_real(deps: Any, body: dict) -> None:
    """대상 계좌 없는 trading kind: 실전 계좌가 있으면 `confirm` 이 그 id 중 하나여야 한다."""
    port = (deps.ports or {}).get("accounts")
    if port is None:
        raise HTTPException(status_code=400, detail="계좌 정보가 없어 실전 확인을 할 수 없습니다")
    real = {
        str(a.id) for a in _all_accounts(port) if str(getattr(a, "env", None) or "real") != "paper"
    }
    if real and body.get("confirm") not in real:
        detail = "실전 계좌가 포함된 작업은 confirm 에 실전 계좌 id 를 적어야 합니다"
        raise HTTPException(status_code=400, detail=detail)


def _enabled(spec: Any, settings: Any) -> bool:
    if spec.enabled is None:
        return True
    try:
        return bool(spec.enabled(settings))
    except AttributeError:  # 설정 섹션이 없는 조립(테스트·부분 부트)은 켜진 것으로 보인다
        return True


def schedule_table(deps: Any, status_jobs: dict[str, dict] | None = None) -> list[dict]:
    """등록 잡 표: 데몬이 올리는 JobSpec 선언(per_market 은 `settings.markets` 로 id 펼침).
    시각·주기 값은 schedule.yaml 이 정하므로 키만 보이고, 다음 실행은 하트비트 `next_run`."""
    markets = list(getattr(deps.settings, "markets", None) or [])
    rows: list[dict] = []
    for feature in with_kernel(deps.features):
        for spec in feature.jobs:
            if not _enabled(spec, deps.settings):
                continue
            for market in markets if spec.per_market else [None]:
                tag = market.lower() if market else None
                ident = spec.id.format(market=tag) if spec.per_market else spec.id
                key = spec.schedule_key
                if key and spec.per_market:
                    key = key.format(market=tag)
                rows.append(
                    {
                        "id": ident,
                        "kind": spec.kind,
                        "trigger": spec.trigger,
                        "schedule_key": key,
                        "interval_s": spec.interval_s,
                        "market": market,
                    }
                )
    if status_jobs is None:
        objs = [
            SimpleNamespace(id=r["id"], kind=r["kind"], market=r["market"], cron=None,
                            interval_s=r["interval_s"])
            for r in rows
        ]  # fmt: skip
        status_jobs = {j["id"]: j for j in read_status(deps.state, deps.now(), objs)["jobs"]}
    for r in rows:
        r.pop("market")
        r["next_at"] = (status_jobs.get(r["id"]) or {}).get("next_run")
    return rows


def _labelled(table: jobq.Kinds, rows: list[dict]) -> list[dict]:
    for r in rows:
        r["label"] = jobq.label(table, r["kind"])
    return rows


@router.get("/jobs")
def list_kinds(request: Request) -> dict:
    """요청 가능한 kind 표 + 등록 잡 표."""
    deps = request.app.state.deps
    table = kinds(deps)
    return {
        "kinds": [
            {
                "kind": k,
                "label": q.label,
                "lane": q.lane,
                "trading": q.trading,
                "params_schema": q.params_model.model_json_schema(),
            }
            for k, q in table.items()
        ],
        "jobs": schedule_table(deps),
    }


@router.get("/jobs/requests")
def list_requests(request: Request, limit: int = Query(30, ge=1, le=200)) -> dict:
    """대기·실행 중(실행 순서) + 최근 종료분. `alive` 는 데몬 생존."""
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        rows = jobq.list_recent(conn, limit=limit)
        active = jobq.active_count(conn)
        rows = activity.annotate(conn, rows, source="job_requests")
    return {"alive": daemon_alive(deps), "active": active, "requests": _labelled(kinds(deps), rows)}


@router.get("/jobs/activity")
def job_activity(request: Request) -> dict:
    """자동 실행 단계·요청 큐·24시간 오류 이력을 별도로 조회한다."""
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        return activity.snapshot(conn, deps.now())


@router.post("/jobs", dependencies=WRITE)
def request_job(request: Request, body: JobBody) -> dict:
    deps = request.app.state.deps
    table = kinds(deps)
    try:
        params = jobq.validate(table, body.kind, body.params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    account_id = params.get("account_id")
    if table[body.kind].trading and account_id:
        require_confirm(body.model_dump(), str(account_id), account_env(deps, str(account_id)))
    elif table[body.kind].trading:
        require_confirm_any_real(deps, body.model_dump())
    if not daemon_alive(deps):
        raise HTTPException(status_code=409, detail=DAEMON_DOWN)
    with closing(deps.conn_factory()) as conn:
        try:
            rid = jobq.enqueue(conn, table, body.kind, params, source="web", now=deps.now())
        except jobq.DuplicateRequest as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        jobq.log_request(
            conn, kind=body.kind, req_id=rid, params=params, channel="web",
            actor=actor_of(request), now=deps.now(),
        )  # fmt: skip
    return {
        "id": rid,
        "kind": body.kind,
        "params": params,
        "status": "queued",
        "label": jobq.label(table, body.kind),
    }


@router.delete("/jobs/{req_id}", dependencies=WRITE, status_code=204)
def cancel_job(request: Request, req_id: int) -> Response:
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        if not jobq.cancel(conn, req_id, now=deps.now(), kinds=kinds(deps)):
            raise HTTPException(status_code=409, detail=CANCEL_ONLY_QUEUED)
        jobq.log_cancel(conn, req_id, channel="web", actor=actor_of(request), now=deps.now())
    return Response(status_code=204)
