"""scalp 웹 조회: `/accounts/{id}/positions` 단타 조각·`GET /system/scalp?market=`(옛
`system.scalp_status`)·SSE `scalp`. 상태 파일
(`state/scalp/<운영일>/<시장>.json`)만 읽는다(브로커·주문 없음).

단타는 모의 전용이라 모의 현물 계좌(`ports["accounts"].accounts()` 중 env paper)이면서 오늘 상태
파일의 다리에 든 계좌만 행이 있다. 손절·목표는 청산 규칙(`stop_pct`·`take_profit_pct`, 판정자가
포지션별로 덮은 값 우선 — `engine.pos_cfg`)을 체결 평단(없으면 신호가)에 적용한 값."""

from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from trader.features.scalp.engine import pos_cfg
from trader.features.scalp.judge import judge_summary
from trader.features.scalp.state import load_state, state_dir
from trader.kernel.ledger import names
from trader.kernel.registry.types import ResourcePart, SseKey
from trader.kernel.session.run_store import run_date_for

CURRENCY = {"KR": "KRW", "US": "USD"}
SOURCE = "단타 규칙"


def _paper_account(deps: Any, account_id: str | None) -> Any | None:
    port = (deps.ports or {}).get("accounts")
    if port is None or not account_id:
        return None
    return next((a for a in port.accounts() if a.id == account_id and a.env == "paper"), None)


def scalp_rows(deps: Any, q: dict[str, str]) -> list[dict]:
    """`ResourcePart` 조각: 이 계좌의 오늘 단타 다리 → 행. 다리가 없으면 빈 목록."""
    acct = _paper_account(deps, q.get("account_id"))
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
    return {
        "enabled": cfg.enabled,
        "judge": cfg.judge,
        "max_weight_pct": cfg.max_weight_pct,
        "max_positions": cfg.max_positions,
        "markets": by,
    }


ROUTERS = (system_scalp_router,)


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
