"""evaluation 웹 조회(토큰 없음). 이 Task 는 세금 로트만 — 성과·분석 자원은 Task 11 이 더한다.

`evaluation_router`: `GET /accounts/{account_id}/tax-lots?market=&year=&format=csv`(옛
`portfolio.lots_router` 이식, 표시 전용). 계좌는 `deps.ports["accounts"].accounts()`(현물만 —
파생·모르는 계좌는 404), market 생략은 계좌 첫 시장."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from trader.features.evaluation import lots
from trader.kernel.web.csv import csv_response

evaluation_router = APIRouter(tags=["evaluation"])

LOT_COLUMNS = [
    "ticker", "qty", "buy_date", "sell_date", "cost", "proceeds", "pnl", "term", "wash_sale",
]  # fmt: skip


def _stock_account(deps: Any, account_id: str) -> Any:
    port = (deps.ports or {}).get("accounts")
    acct = next((a for a in (port.accounts() if port else []) if a.id == account_id), None)
    if acct is None:
        raise HTTPException(status_code=404, detail=f"알 수 없는 현물 계좌: {account_id}")
    return acct


@evaluation_router.get("/accounts/{account_id}/tax-lots")
def tax_lots(
    request: Request,
    account_id: str,
    market: str | None = None,
    year: int | None = None,
    format: str | None = None,
):
    """세금 로트·워시세일(표시 전용): FIFO 실현 로트·보유 로트·요약.
    `?format=csv` 는 실현 로트 표."""
    deps = request.app.state.deps
    acct = _stock_account(deps, account_id)
    m = (market or "").upper() or acct.markets[0]
    if m not in acct.markets:
        raise HTTPException(status_code=400, detail=f"계좌 {acct.id} 에 없는 시장: {m}")
    with closing(deps.conn_factory()) as conn:
        out = lots.report(conn, acct.id, m, year=year)
    if format == "csv":
        suffix = f"_{year}" if year else ""
        return csv_response(out["realized"], f"tax_lots_{acct.id}_{m}{suffix}.csv", LOT_COLUMNS)
    return out


ROUTERS = (evaluation_router,)
