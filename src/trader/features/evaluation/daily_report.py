"""장 마감 일일 리포트(Stock D-095 교훈): 하루가 어떻게 끝났는지 한 메시지로 — 계좌·시장별
NAV·전일 대비·최근 5거래일 실현손익·오늘 주문(상태별)·살아 있는 주문·보유 종목 수, 그리고
킬스위치 한 줄. 읽기 전용. 데몬 cron `<시장>_daily_report`(시장 마감 뒤)·텔레그램 `/daily`.

NAV 는 `account_nav` 의 그날 행이다(없으면 그 계좌·시장은 뺀다). 보유 수는 그날 현물
`positions_snapshot` 의 qty>0 행 수(행이 없으면 0 — 전량 현금인 날)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from trader.features.regime.api import killswitch_reason
from trader.kernel.execution import ledger as repo
from trader.kernel.execution.types import OPEN_STATUSES as OPEN
from trader.kernel.notify import messages


def _row(conn: sqlite3.Connection, account_id: str, market: str, d: date) -> dict | None:
    nav_row = conn.execute(
        "SELECT nav FROM account_nav WHERE account_id=? AND market=? AND date=?",
        (account_id, market, d.isoformat()),
    ).fetchone()
    if nav_row is None:
        return None
    nav = Decimal(str(nav_row["nav"]))
    prev = repo.latest_nav_before(conn, account_id, d, market=market)
    lo, hi = repo.day_bounds(d, market)
    orders: dict[str, int] = {}
    for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM orders WHERE account_id=? AND market=? "
        "AND created_at >= ? AND created_at < ? GROUP BY status",
        (account_id, market, lo, hi),
    ):
        orders[r["status"]] = int(r["n"])
    marks = ",".join("?" * len(OPEN))
    n_open = int(
        conn.execute(
            f"SELECT COUNT(*) FROM orders WHERE account_id=? AND market=? AND status IN ({marks})",
            (account_id, market, *OPEN),
        ).fetchone()[0]
    )
    holdings = int(
        conn.execute(
            "SELECT COUNT(*) FROM positions_snapshot WHERE account_id=? AND market=? AND date=? "
            "AND asset='stock' AND qty>0",
            (account_id, market, d.isoformat()),
        ).fetchone()[0]
    )
    return {
        "account_id": account_id,
        "market": market,
        "nav": str(nav),
        "change_pct": float(nav / prev - 1) if prev and prev > 0 else None,
        "realized_5d": str(repo.realized_pnl_5d(conn, account_id, d, market=market)),
        "orders": orders,
        "open": n_open,
        "holdings": holdings,
    }


def compute(
    conn: sqlite3.Connection,
    accounts: list,
    *,
    markets: list[str],
    today_of: Callable[[str], date],
) -> dict:
    """{"items": [...], "killswitch": 사유|None}. today_of(market) 은 그 시장의 운영일."""
    items = []
    for a in accounts:
        for m in a.markets:
            if m not in markets:
                continue
            row = _row(conn, a.id, m, today_of(m))
            if row is not None:
                items.append(row)
    return {"items": items, "killswitch": killswitch_reason(conn)}


def message(r: dict) -> str:
    lines = []
    for it in r["items"]:
        chg = "—" if it["change_pct"] is None else f"{it['change_pct']:+.2%}"
        orders = ", ".join(f"{k} {v}" for k, v in sorted(it["orders"].items())) or "없음"
        lines.append(
            f"{it['account_id']} {it['market']}: NAV {messages.money(it['nav'], it['market'])} "
            f"({chg}) · 5일 실현 {messages.money(it['realized_5d'], it['market'])} · "
            f"보유 {it['holdings']} · 오늘 주문 {orders} · 미결 {it['open']}"
        )
    if r.get("killswitch"):
        lines.append(f"킬스위치 켜짐: {r['killswitch']}")
    return messages.event("일일 리포트", "", *lines)
