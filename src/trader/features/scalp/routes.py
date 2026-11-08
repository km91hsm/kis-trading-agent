"""scalp 웹: `/accounts/{id}/positions` 단타 조각·`GET /system/scalp?market=`(옛
`system.scalp_status`)·`GET·PATCH /settings/scalp`(계좌별 스위치)·SSE `scalp`. 상태 파일
(`state/scalp/<운영일>/<시장>.json`)만 읽는다(브로커·주문 없음).

현물 계좌(`ports["accounts"].accounts()`)이면서 오늘 상태 파일의 다리에 든 계좌만 행이 있다.
손절·목표는 청산 규칙(`stop_pct`·`take_profit_pct`, 판정자가 포지션별로
덮은 값 우선 — `engine.pos_cfg`)을 체결 평단(없으면 신호가)에 적용한 값."""

from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from trader.features.scalp import switch
from trader.features.scalp.engine import pos_cfg
from trader.features.scalp.judge import judge_summary
from trader.features.scalp.state import load_state, state_dir
from trader.kernel.ledger import names
from trader.kernel.ledger.control import delete_flag, log_change, set_flag
from trader.kernel.registry.types import ResourcePart, SseKey
from trader.kernel.session.run_store import run_date_for
from trader.kernel.web.auth import actor_of, require_confirm, require_same_origin, require_token

WRITE = [Depends(require_token), Depends(require_same_origin)]
CURRENCY = {"KR": "KRW", "US": "USD"}
SOURCE = "단타 규칙"


def _spot_account(deps: Any, account_id: str | None) -> Any | None:
    port = (deps.ports or {}).get("accounts")
    if port is None or not account_id:
        return None
    return next((a for a in port.accounts() if a.id == account_id), None)


def scalp_rows(deps: Any, q: dict[str, str]) -> list[dict]:
    """`ResourcePart` 조각: 이 계좌의 오늘 단타 다리 → 행. 다리가 없으면 빈 목록."""
    acct = _spot_account(deps, q.get("account_id"))
    if acct is None:
        return []
    cfg = deps.settings.scalp
    market = (q.get("market") or "").upper()
    out = []
    with closing(deps.conn_factory()) as conn:
        for m in [market] if market else list(acct.markets):
            st = load_state(deps.state, run_date_for(m, deps.now()), m)
            for pos in st.get("open") or []:
                leg = (pos.get("legs") or {}).get(acct.id)
                if leg is None:
                    continue
                qty = int((pos.get("filled") or {}).get(acct.id, leg.get("req") or 0))
                avg = Decimal(str(pos.get("entry_fill") or pos["entry_price"]))
                rule = pos_cfg(cfg, pos)
                out.append({
                    "account_id": acct.id, "asset": "stock", "lane": "scalp", "market": m,
                    "ticker": pos["ticker"], "name": names.resolve(conn, m, pos["ticker"]),
                    "qty": qty, "avg_price": str(avg), "last_price": None,
                    "currency": CURRENCY.get(m, "KRW"),
                    "stop": {"level": str(avg * (1 - Decimal(str(rule.stop_pct)))),
                             "source": SOURCE},
                    "target": {"level": str(avg * (1 + Decimal(str(rule.take_profit_pct)))),
                               "source": SOURCE},
                    "status": pos.get("status"), "exit_reason": pos.get("exit_reason"),
                    "at": pos.get("entry_at"),
                })  # fmt: skip
    return out


RESOURCES = (ResourcePart("/accounts/{account_id}/positions", scalp_rows),)

system_scalp_router = APIRouter(prefix="/system", tags=["system"])
settings_scalp_router = APIRouter(tags=["settings"])


def _accounts_view(deps: Any, conn: Any) -> list[dict]:
    """현물 계좌별 스위치 행(저장값·출처·실효·사유)."""
    port = (deps.ports or {}).get("accounts")
    if port is None:
        return []
    out = []
    for a in port.accounts():
        enabled, source = switch.stored(conn, deps.settings, a)
        effective, reason = switch.account_enabled(conn, deps.settings, a, port)
        out.append({
            "id": a.id, "env": a.env, "markets": list(a.markets), "enabled": enabled,
            "source": source, "effective": effective, "reason": reason,
        })  # fmt: skip
    return out


def _settings_view(deps: Any) -> dict:
    cfg = deps.settings.scalp
    with closing(deps.conn_factory()) as conn:
        accounts = _accounts_view(deps, conn)
    return {
        "accounts": accounts, "judge": cfg.judge, "max_positions": cfg.max_positions,
        "max_weight_pct": cfg.max_weight_pct, "default_paper": cfg.enabled,
    }  # fmt: skip


class ScalpSwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool | None  # None 이면 저장값을 지워 기본값으로
    confirm: str | None = None


@settings_scalp_router.get("/settings/scalp")
def get_scalp_settings(request: Request) -> dict:
    return _settings_view(request.app.state.deps)


@settings_scalp_router.patch("/settings/scalp/{account_id}", dependencies=WRITE)
def patch_scalp_switch(request: Request, account_id: str, body: ScalpSwitchBody) -> dict:
    """계좌별 단타 켜기·끄기·기본값 복귀. 실전 켜기는 `confirm == 계좌 id`. 바뀌면 control 이력."""
    deps = request.app.state.deps
    acct = _spot_account(deps, account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail=f"없는 계좌: {account_id}")
    if body.enabled is True:
        require_confirm(body.model_dump(), account_id, acct.env)
    key = switch.flag_key(account_id)
    actor = actor_of(request)
    now = deps.now().isoformat()
    with closing(deps.conn_factory()) as conn:
        old = switch.stored(conn, deps.settings, acct)
        if body.enabled is None:
            delete_flag(conn, key)
        elif body.enabled and acct.env != "paper":
            set_flag(conn, key, {"enabled": True, "confirm": account_id, "at": now, "actor": actor})
        else:
            set_flag(conn, key, body.enabled)
        new = switch.stored(conn, deps.settings, acct)
        if old[0] != new[0]:  # 실효 저장값이 바뀐 경우만(출처만 바뀌면 기록 없음)
            log_change(
                conn, channel="web", actor=actor, key=key, old=old[0], new=new[0], at=now,
            )  # fmt: skip
    return _settings_view(deps)


@system_scalp_router.get("/scalp")
def scalp_status(request: Request, market: str | None = None) -> dict:
    """단타 상태: 설정·시장별 오늘 진입/청산·중단 사유·판정 대기·판정자 사후 비교
    (분봉 조회 없음)."""
    deps = request.app.state.deps
    cfg = deps.settings.scalp
    if market is not None and market not in deps.settings.markets:
        raise HTTPException(status_code=400, detail=f"운영하지 않는 시장: {market}")
    by = {}
    for m in [market] if market else list(deps.settings.markets):
        d = run_date_for(m, deps.now())
        st = load_state(deps.state, d, m)
        by[m] = {
            "date": d.isoformat(),
            "open": st.get("open") or [],
            "closed": st.get("closed") or [],
            "halted": st.get("halted"),
            "pending": sorted((st.get("pending") or {}).keys()),
            "judge": judge_summary(deps.state, market=m, days=[d], cfg=cfg),
        }
    port = (deps.ports or {}).get("accounts")
    with closing(deps.conn_factory()) as conn:
        enabled = port is not None and switch.any_enabled(conn, deps.settings, port)
        accounts = _accounts_view(deps, conn)
    return {
        "enabled": enabled,
        "accounts": accounts,
        "judge": cfg.judge,
        "max_weight_pct": cfg.max_weight_pct,
        "max_positions": cfg.max_positions,
        "markets": by,
    }


ROUTERS = (system_scalp_router, settings_scalp_router)


def _scalp_fp(deps: Any) -> dict[str, str]:
    """시장별 오늘 상태 파일 mtime(ns) + 보유 수 `"<mtime>|<n>"`(파일 없으면 `"0|0"`)."""
    out = {}
    for m in deps.settings.markets:
        d = run_date_for(m, deps.now())
        p = state_dir(deps.state, d) / f"{m}.json"
        if not p.exists():
            out[m] = "0|0"
            continue
        out[m] = f"{p.stat().st_mtime_ns}|{len(load_state(deps.state, d, m).get('open') or [])}"
    return out


SSE_KEYS = (SseKey("scalp", _scalp_fp),)
