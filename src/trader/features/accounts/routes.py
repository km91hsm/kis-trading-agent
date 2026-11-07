"""accounts 웹 자원. 조회는 토큰 없음, 변경은 토큰 + 같은 출처, yaml·연결 키 편집은
이 PC 직접 접속만.

`accounts_router`: `GET /accounts`, `GET /accounts/overview`,
`PATCH /accounts/{id} {mode, confirm?}`, `POST /accounts`, `DELETE /accounts/{id}`.
`settings_router`: `GET /settings`, `PATCH /settings/paused {value, confirm?}`,
`GET|POST /settings/secrets`.
계좌는 `deps.ports["accounts"]`(현물 `accounts()` + 파생 `fo_accounts()`, 없으면 계좌 없음).
yaml·키를 바꾼 결과는 실행 중인 데몬·웹이 시작할 때 읽은 값을 쓰므로 재시작 뒤 적용된다."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from contextlib import closing
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from trader.features.accounts import accounts_io, modes, overview, secrets_io
from trader.features.accounts.accounts_io import AccountSpec
from trader.features.accounts.settings import Account, Mode, load_accounts
from trader.features.quotes import api as quotes_api
from trader.kernel.config import ConfigError
from trader.kernel.config.secrets import Secrets
from trader.kernel.ledger import control
from trader.kernel.platform import paths
from trader.kernel.registry.types import SseKey
from trader.kernel.web.auth import (
    actor_of,
    require_confirm,
    require_direct_local,
    require_same_origin,
    require_token,
)

logger = logging.getLogger("trader.accounts")

WRITE = [Depends(require_token), Depends(require_same_origin)]
LOCAL_WRITE = [*WRITE, Depends(require_direct_local)]
accounts_router = APIRouter(tags=["accounts"])
settings_router = APIRouter(tags=["settings"])


class ModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode
    confirm: str | None = None


class PausedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: bool
    confirm: str | None = None


class SecretsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, str] = {}
    clear: list[str] = []


def _accounts(deps: Any) -> list[Account]:
    """현물 + 파생 계좌(설정 화면은 파생 계좌 모드도 바꾼다). 포트가 없으면 빈 목록."""
    port = (deps.ports or {}).get("accounts")
    if port is None:
        return []
    fo = getattr(port, "fo_accounts", None)
    return [*port.accounts(), *(fo() if fo else [])]


def _find(deps: Any, account_id: str) -> Account:
    acct = next((a for a in _accounts(deps) if a.id == account_id), None)
    if acct is None:
        raise HTTPException(status_code=404, detail=f"알 수 없는 계좌: {account_id}")
    return acct


def _accounts_yaml():
    return paths.config_dir() / "accounts.yaml"


def _summary(acct: Any) -> dict:
    """조작 이력용 계좌 요약(env·asset·markets·mode) — 계좌번호·키 이름은 넣지 않는다."""
    return {
        "env": acct.env,
        "asset": acct.asset,
        "markets": list(acct.markets),
        "mode": acct.mode,
    }


def _log(request: Request, key: str, old: Any, new: Any) -> None:
    """웹 조작 이력(control_log, 스펙 §5). PATCH 모드·일시정지와 같은 channel·actor."""
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        control.log_change(
            conn, channel="web", actor=actor_of(request), key=key, old=old, new=new,
            at=deps.now().isoformat(),
        )  # fmt: skip


# --- accounts ---------------------------------------------------------------------------


@accounts_router.get("/accounts")
def list_accounts(request: Request) -> list[dict]:
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        return overview.account_rows(
            conn, deps.settings, _accounts(deps), now=deps.now(), paused=modes.is_paused(conn)
        )


@accounts_router.get("/accounts/overview")
def accounts_overview(request: Request) -> dict:
    deps = request.app.state.deps
    quotes_api.prepare_fx(deps)  # KIS 환율 원천 먼저(없으면 FRED)
    with closing(deps.conn_factory()) as conn:
        return overview.totals(
            conn, deps.settings, _accounts(deps), now=deps.now(), paused=modes.is_paused(conn)
        )


@accounts_router.patch("/accounts/{account_id}", dependencies=WRITE)
def set_mode(request: Request, account_id: str, body: ModeBody) -> dict:
    """실전 계좌는 `confirm` 에 계좌 id. 실전 auto 는 그 재입력을 LIVE 확인으로 삼아 기록을
    남긴다(channel "web"·actor, 사용자 결정 T6-R1 변경).

    이미 LIVE 확인 기록이 있으면 다시 쓰지 않는다: 기록은 auto 일 때만 남으므로(다른 모드를 쓰면
    지워진다) 그 요청은 이미 auto 인 계좌의 재확인 — 처음 준 시각·주체를 지키고 현재 값을
    돌려준다."""
    deps = request.app.state.deps
    acct = _find(deps, account_id)
    require_confirm(body.model_dump(), acct.id, acct.env)
    with closing(deps.conn_factory()) as conn:
        live = acct.env == "real" and body.mode == "auto"
        if not (live and modes.live_confirmed(conn, acct.id) is not None):
            modes.set_account_mode(
                conn, acct, body.mode, confirm=modes.LIVE_CONFIRM if live else None,
                channel="web", actor=actor_of(request),
            )  # fmt: skip
        return {"account_id": acct.id, "mode": modes.get_account_mode(conn, acct)}


@accounts_router.post("/accounts", dependencies=LOCAL_WRITE)
def add_account(request: Request, spec: AccountSpec) -> dict:
    """config/accounts.yaml 에 계좌 한 줄(계좌번호는 자리표시자 — 실제 번호는 연결 키)."""
    try:
        r = accounts_io.add_account(_accounts_yaml(), spec)
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # 이력은 실제로 적힌 줄(실전은 mode off, 파생은 KR 만 — `accounts_io` 규칙)을 다시 읽어 요약한다
    written = load_accounts(_accounts_yaml(), secrets=Secrets())
    _log(request, f"account:{spec.id}", None,
         _summary(next((a for a in written if a.id == spec.id), spec)))  # fmt: skip
    logger.info("계좌 추가(웹): %s (%s·%s)", spec.id, spec.env, spec.asset)
    return {**r, "restart_required": True}


@accounts_router.delete("/accounts/{account_id}", dependencies=LOCAL_WRITE)
def remove_account(request: Request, account_id: str) -> dict:
    """살아 있는 주문·승인·보유가 있으면 409. 이력 old 는 실행 중 계좌 목록의 요약(재시작 전에
    추가한 계좌면 목록에 없어 None)."""
    deps = request.app.state.deps
    before = next((a for a in _accounts(deps) if a.id == account_id), None)
    try:
        with closing(deps.conn_factory()) as conn:
            r = accounts_io.remove_account(_accounts_yaml(), account_id, conn)
    except accounts_io.AccountInUse as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _log(request, f"account:{account_id}", None if before is None else _summary(before), None)
    logger.info("계좌 삭제(웹): %s", account_id)
    return {**r, "restart_required": True}


# --- settings ---------------------------------------------------------------------------


@settings_router.get("/settings")
def settings_view(request: Request) -> dict:
    """설정 요약. 토큰 없는 조회라 tailnet 사용자 이메일·호스트명은 수만 돌려준다."""
    s = request.app.state.deps.settings
    risk = getattr(s, "risk", None)
    from trader.kernel.session import research_policy

    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        codex_debate = research_policy.status(conn, s, now=deps.now())
    return {
        "markets": list(s.markets),
        "risk": risk.model_dump(mode="json") if risk is not None else None,
        "debate_rounds": getattr(s, "debate_rounds", None),
        "models": dict(s.models),
        "web": {
            "bind": s.web.bind,
            "port": s.web.port,
            "tailscale_hosts_count": len(s.web.tailscale_hosts),
            "tailscale_users_count": len(s.web.tailscale_users),
        },
        "llm": {
            "budget_usd": s.llm.budget_usd,
            "daily_session_cap": s.llm.daily_session_cap,
            "codex_debate": codex_debate,
        },
    }


@settings_router.patch("/settings/paused", dependencies=WRITE)
def set_paused(request: Request, body: PausedBody) -> dict:
    """일시정지는 확인 없이, 해제는 실전 auto 계좌가 있으면 `confirm` 에 그 계좌 id 중 하나."""
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        if not body.value:
            live = [
                a.id
                for a in _accounts(deps)
                if a.env == "real" and modes.account_mode(conn, a)[0] == "auto"
            ]
            if live and body.confirm not in live:
                raise HTTPException(
                    status_code=400,
                    detail="실전 auto 계좌가 있어 재개하려면 confirm 에 그 계좌 id 를 "
                    "적어야 합니다: " + ", ".join(live),
                )
        modes.set_paused(conn, body.value, channel="web", actor=actor_of(request))
        return {"ok": True, "paused": modes.is_paused(conn)}


@settings_router.get(
    "/settings/secrets", dependencies=[Depends(require_token), Depends(require_direct_local)]
)
def secrets_status(request: Request) -> dict:
    """연결 키 '설정됨' 여부만(값은 돌려주지 않는다)."""
    return secrets_io.status(paths.secrets_path(), _accounts(request.app.state.deps))


@settings_router.post("/settings/secrets", dependencies=LOCAL_WRITE)
def secrets_update(request: Request, body: SecretsBody) -> dict:
    try:
        r = secrets_io.update(
            paths.secrets_path(),
            _accounts(request.app.state.deps),
            values=body.values,
            clear=body.clear,
        )
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if r["updated"] or r["cleared"]:  # 키 이름 목록만 — 값은 절대 기록하지 않는다
        _log(request, "secrets:env", None, {"updated": r["updated"], "cleared": r["cleared"]})
    logger.info("연결 키 변경(웹): 설정 %s · 지움 %s", r["updated"], r["cleared"])
    return {**r, "restart_required": bool(r["updated"] or r["cleared"])}


ROUTERS = (accounts_router, settings_router)


# --- SSE 지문 ---------------------------------------------------------------------------


def _digest(rows: list[sqlite3.Row]) -> str:
    return hashlib.sha1(repr([tuple(r) for r in rows]).encode("utf-8")).hexdigest()[:16]


def _accounts_fp(deps: Any) -> str:
    """계좌 모드 지문(account_modes 전 행) + paused."""
    with closing(deps.conn_factory()) as conn:
        rows = conn.execute(
            "SELECT account_id, mode, live_confirmed_json, updated_at FROM account_modes "
            "ORDER BY account_id"
        ).fetchall()
        return f"{_digest(rows)}|{int(modes.is_paused(conn))}"


def _positions_fp(deps: Any) -> str:
    """잔고 지문: 행 수·최대 id + 최근 사흘(가장 늦은 날짜 기준) 보유·NAV·현금 값 해시.
    관측 시각(created_at)은 넣지 않는다."""
    with closing(deps.conn_factory()) as conn:
        head = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM positions_snapshot"
        ).fetchone()
        pos = conn.execute(
            "SELECT account_id, date, market, asset, ticker, qty, avg_price, current_price, "
            "eval_amount FROM positions_snapshot WHERE date >= "
            "(SELECT date(MAX(date), '-3 day') FROM positions_snapshot) ORDER BY id"
        ).fetchall()
        nav = conn.execute(
            "SELECT account_id, date, market, nav, cash FROM account_nav WHERE date >= "
            "(SELECT date(MAX(date), '-3 day') FROM account_nav) ORDER BY id"
        ).fetchall()
    return f"{head[0]}:{head[1]}|{_digest(pos)}|{_digest(nav)}"


def _approvals_fp(deps: Any) -> str:
    """pending 수 + 건수·최대 id·상태별 건수(결정·만료 같은 UPDATE 도 잡는다)."""
    with closing(deps.conn_factory()) as conn:
        head = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM approvals").fetchone()
        groups = conn.execute(
            "SELECT status, COUNT(*) FROM approvals GROUP BY status ORDER BY status"
        ).fetchall()
    by = dict((g[0], g[1]) for g in groups)
    return f"{by.get('pending', 0)}|{head[0]}:{head[1]}:" + ",".join(
        f"{k}={v}" for k, v in by.items()
    )


SSE_KEYS = (
    SseKey("accounts", _accounts_fp),
    SseKey("positions", _positions_fp),
    SseKey("approvals", _approvals_fp),
)
