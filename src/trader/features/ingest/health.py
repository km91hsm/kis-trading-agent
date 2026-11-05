"""ingest 운영 점검 영역: 현물 계좌 보유 종목(최신 스냅샷)의 최근 3일 critical 공시 → critical."""

from __future__ import annotations

from datetime import timedelta

from trader.features.ingest.disclosures import critical_recent
from trader.kernel.execution.ledger import latest_positions
from trader.kernel.ops.types import Item, OpsContext
from trader.kernel.registry.types import HealthArea

DISCLOSURE_DAYS = 3


def check(ctx: OpsContext) -> list[Item]:
    port = ctx.ports.get("accounts")
    accounts = port.accounts() if port is not None else []
    accounts = [a for a in accounts if getattr(a, "asset", "stock") == "stock"]
    out: list[Item] = []
    for market in ctx.settings.markets:
        held: set[str] = set()
        for a in accounts:
            if market not in a.markets:
                continue
            snap = latest_positions(ctx.conn, a.id, market)
            held |= {p["ticker"] for p in (snap or {}).get("positions") or [] if p["qty"]}
        if not held:
            continue
        since = ctx.now - timedelta(days=DISCLOSURE_DAYS)
        rows = critical_recent(ctx.conn, market, since=since, tickers=held)
        if rows:
            out.append(
                Item(
                    f"ingest.disclosure.{market}",
                    "critical",
                    f"{market} 보유 종목 중요 공시 {len(rows)}건 — {rows[-1]['title']}",
                    link="/data",
                    count=len(rows),
                )
            )
    return out


AREA = HealthArea("ingest", check)
