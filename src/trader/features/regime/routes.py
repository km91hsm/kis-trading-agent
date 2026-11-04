"""regime 「시스템」 웹 조회(토큰 없음·읽기 전용): `GET /system/regime?market=`(옛
`system.regime_status`) — 헤지 설정 요약 + 시장별 국면·오늘 시장 판단(`view`)·적중률(`summary`)
+ 킬스위치 상태. market 을 주면 그 시장만(운영 시장이 아니면 400)."""

from __future__ import annotations

from contextlib import closing

from fastapi import APIRouter, HTTPException, Request

from trader.features.regime import killswitch, regime
from trader.kernel.config import KST

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


ROUTERS = (system_regime_router,)
