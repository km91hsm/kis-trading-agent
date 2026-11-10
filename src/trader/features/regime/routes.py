"""regime 「시스템」 웹 조회(토큰 없음·읽기 전용): `GET /system/regime?market=`(옛
`system.regime_status`) — 헤지 설정 요약 + 시장별 국면·오늘 시장 판단(`view`)·적중률(`summary`)
+ 킬스위치 상태. market 을 주면 그 시장만(운영 시장이 아니면 400)."""

from __future__ import annotations

from contextlib import closing
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from trader.features.regime import killswitch, market_view, policy, regime
from trader.kernel.config import KST
from trader.kernel.session.run_store import run_date_for
from trader.kernel.web.auth import actor_of, require_confirm, require_same_origin, require_token

system_regime_router = APIRouter(prefix="/system", tags=["system"])

KILLSWITCH_KEYS = ("reason", "source", "accounts", "at")


@system_regime_router.get("/regime")
def regime_status(request: Request, market: str | None = None) -> dict:
    deps = request.app.state.deps
    if market is not None and market not in deps.settings.markets:
        raise HTTPException(status_code=400, detail=f"운영하지 않는 시장: {market}")
    h = deps.settings.hedge
    markets = [market] if market else list(deps.settings.markets)
    today = deps.now().astimezone(KST).date()
    with closing(deps.conn_factory()) as conn:
        rows = regime.summary(conn, markets, today=today)
        ks = killswitch.state(conn)
    return {
        "enabled": h.enabled,
        "max_weight_pct": h.max_weight_pct,
        "leverage": h.leverage,
        "bear_min_signals": h.bear_min_signals,
        "market_session": h.market_session,
        "min_weight_pct": h.min_weight_pct,
        "instruments": {
            m: {k: v.model_dump() for k, v in d.items()} for m, d in h.instruments.items()
        },
        "markets": rows,
        "killswitch": {"on": bool(ks.get("on")), **{k: ks.get(k) for k in KILLSWITCH_KEYS}},
    }


risk_policy_router = APIRouter(prefix="/settings", tags=["settings"])


class RiskPolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["AUTO", "RISK_ON", "RISK_OFF"]
    reason: str = Field(min_length=1, max_length=500)
    confirmations: dict[str, str] = Field(default_factory=dict)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("변경 사유를 입력하세요")
        return value.strip()


def _spot_accounts(deps, market: str) -> list:
    port = (deps.ports or {}).get("accounts")
    return [a for a in (port.accounts() if port else [])
            if getattr(a, "asset", "stock") == "stock" and market in a.markets]


@risk_policy_router.get("/risk-policy")
def risk_policy_status(request: Request) -> dict:
    deps = request.app.state.deps
    now = deps.now()
    rows = []
    with closing(deps.conn_factory()) as conn:
        for market in deps.settings.markets:
            r = regime.load(conn, market) or {}
            summary = regime.summary(conn, [market], today=run_date_for(market, now))[0]
            rows.append({"market": market, "regime": r.get("state"), "asof": r.get("asof"),
                         "signals": summary["signals"], "values": summary["values"],
                         "streak": r.get("streak"),
                         "exit_streak_days": deps.settings.hedge.exit_streak_days,
                         "ai_view": market_view.load_view(conn, market, run_date_for(market, now)),
                         **policy.context(conn, market, now, deps.settings),
                         "accounts": [{"id": a.id, "env": a.env}
                                      for a in _spot_accounts(deps, market)],
                         "history": policy.history(conn, market)})
    return {"markets": rows}


@risk_policy_router.patch("/risk-policy/{market}",
                          dependencies=[Depends(require_token), Depends(require_same_origin)])
def set_risk_policy(request: Request, market: str, body: RiskPolicyBody) -> dict:
    deps = request.app.state.deps
    if market not in deps.settings.markets:
        raise HTTPException(status_code=400, detail=f"운영하지 않는 시장: {market}")
    for account in _spot_accounts(deps, market):
        require_confirm({"confirm": body.confirmations.get(account.id)}, account.id, account.env)
    with closing(deps.conn_factory()) as conn:
        policy.save(conn, market, body.mode, body.reason, now=deps.now(), actor=actor_of(request))
    return risk_policy_status(request)


ROUTERS = (system_regime_router, risk_policy_router)
