"""`/status`·STATUS.md 의 spot 절: 최근 결정·미승인 주문·성향.

레인이라 계좌는 `ctx.ports["accounts"]` 로만 본다(포트가 없으면 계좌 줄을 내지 않는다)."""

from __future__ import annotations

from typing import Any

from trader.features.spot import stance
from trader.features.spot.approvals import env_of
from trader.features.spot.messages import approval_line
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import names
from trader.kernel.platform import paths
from trader.kernel.registry.types import StatusSection
from trader.kernel.session.run_store import run_date_for

RECENT = 3


def _accounts(ctx: Any) -> list:
    port = ctx.ports.get("accounts")
    return list(port.accounts()) if port is not None else []


def recent_decisions(ctx: Any) -> list[str]:
    """원장 `decisions` 최근 3건(날짜·id 역순): '- 2026-11-05 KR 삼성전자(005930) BUY'."""
    rows = ctx.conn.execute(
        "SELECT date, market, ticker, action FROM decisions ORDER BY date DESC, id DESC LIMIT ?",
        (RECENT,),
    ).fetchall()
    if not rows:
        return ["- 최근 결정 없음"]
    cache_dir = (ctx.state or paths.state_dir()) / "cache"
    return [
        f"- {r['date']} {r['market']} "
        f"{names.display(ctx.conn, cache_dir, r['market'], r['ticker'])} {r['action']}"
        for r in rows
    ]


def pending_lines(ctx: Any) -> list[str]:
    """승인 대기 요청마다 `approval_line` 한 줄. 없으면 [] (절 생략)."""
    pending = xledger.pending_approvals(ctx.conn)
    if not pending:
        return []
    accounts = _accounts(ctx)
    return [f"- {approval_line(a, env_of(accounts, a['account_id']))}" for a in pending]


def stance_lines(ctx: Any) -> list[str]:
    """현물 계좌×시장 성향(최근 5거래일 실현손익 기준): '- paper-main KR normal'."""
    out = []
    for a in _accounts(ctx):
        if getattr(a, "asset", "stock") != "stock":
            continue
        for m in a.markets:
            pnl = xledger.realized_pnl_5d(ctx.conn, a.id, run_date_for(m, ctx.now()), market=m)
            out.append(f"- {a.id} {m} {stance.decide(pnl)}")
    return out


STATUS_SECTIONS = (
    StatusSection("최근 결정", recent_decisions),
    StatusSection("미승인 주문", pending_lines),
    StatusSection("성향", stance_lines),
)
