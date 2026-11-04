"""계좌 현황 조립: 계좌 행(레인·통화·NAV·현금·일일 손익·보유·승인 수)과 통화별 통합 합계.

금액은 원장 TEXT 를 `Decimal` 로 읽어 `str(Decimal)` 로 내보낸다. `*_at` 은 `account_nav.created_at`
(저장 시각 UTC ISO8601) 그대로다. 계좌번호(cano)는 내보내지 않는다."""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from trader.features.accounts.modes import account_mode, mode_source
from trader.features.accounts.settings import Account
from trader.features.quotes.api import fx_rate
from trader.kernel.execution import ledger as xledger
from trader.kernel.session.run_store import run_date_for

Lane = Literal["spot", "scalp", "fo"]
FX_PAIR = "USDKRW"


def lane_of(settings: Any, account: Account) -> Lane:
    """레인 판정: asset fo → fo, `settings.scalp.accounts` 에 있으면 scalp, 아니면 spot.
    scalp 설정은 덕 타이핑으로 읽는다(accounts 는 레인을 import 하지 않는다)."""
    if account.asset == "fo":
        return "fo"
    scalp_ids = getattr(getattr(settings, "scalp", None), "accounts", None) or ()
    return "scalp" if account.id in scalp_ids else "spot"


def currency_of(account: Account) -> Literal["KRW", "USD"]:
    """markets 가 US 만이면 USD, 그 밖(KR 포함)은 KRW — 혼합 계좌는 행을 나누지 않는다."""
    return "USD" if list(account.markets) == ["US"] else "KRW"


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(v)


def _latest_nav(conn: sqlite3.Connection, account_id: str, market: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT date, nav, cash, created_at FROM account_nav WHERE account_id=? AND market=? "
        "ORDER BY date DESC, id DESC LIMIT 1",
        (account_id, market),
    ).fetchone()


def _day_pnl(
    conn: sqlite3.Connection, account_id: str, market: str, row: sqlite3.Row | None, now: datetime
) -> Decimal | None:
    """오늘(시장 운영일) NAV − 전일 마지막 NAV. 오늘 스냅샷이나 전일 값이 없으면 None."""
    today = run_date_for(market, now)
    if row is None or row["date"] != today.isoformat():
        return None
    prev = xledger.latest_nav_before(conn, account_id, today, market)
    return None if prev is None else Decimal(row["nav"]) - prev


def _positions_count(conn: sqlite3.Connection, account: Account) -> int:
    n = 0
    for m in account.markets:
        snap = xledger.latest_positions(conn, account.id, m, asset=account.asset)
        n += len(snap["positions"]) if snap else 0
    return n


def account_rows(
    conn: sqlite3.Connection,
    settings: Any,
    accounts: list[Account],
    *,
    now: datetime,
    paused: bool,
) -> list[dict]:
    """`GET /accounts` 행 목록(계좌 순서 유지)."""
    pending = Counter(a["account_id"] for a in xledger.pending_approvals(conn))
    out = []
    for a in accounts:
        currency = currency_of(a)
        market = "US" if currency == "USD" else "KR"
        row = _latest_nav(conn, a.id, market)
        us = _latest_nav(conn, a.id, "US") if currency == "KRW" and "US" in a.markets else None
        mode, note = account_mode(conn, a)
        out.append(
            {
                "id": a.id,
                "env": a.env,
                "lane": lane_of(settings, a),
                "markets": list(a.markets),
                "currency": currency,
                "mode": mode,
                "mode_note": note,
                "mode_source": mode_source(conn, a.id),
                "paused": paused,
                "nav": _s(Decimal(row["nav"])) if row else None,
                "nav_at": row["created_at"] if row else None,
                "cash": _s(Decimal(row["cash"])) if row else None,
                "cash_at": row["created_at"] if row else None,
                "usd_nav": _s(Decimal(us["nav"])) if us else None,
                "day_pnl": _s(_day_pnl(conn, a.id, market, row, now)),
                "positions_count": _positions_count(conn, a),
                "pending_approvals": pending.get(a.id, 0),
            }
        )
    return out


def _market_total(
    conn: sqlite3.Connection, accounts: list[Account], market: str, now: datetime
) -> dict:
    """시장(통화) 하나의 NAV·일일 손익 합계와 스냅샷이 있는 계좌 id. 값이 하나도 없으면 None."""
    nav: Decimal | None = None
    pnl: Decimal | None = None
    ids: list[str] = []
    for a in accounts:
        if market not in a.markets:
            continue
        row = _latest_nav(conn, a.id, market)
        if row is None:
            continue
        ids.append(a.id)
        nav = (nav or Decimal(0)) + Decimal(row["nav"])
        d = _day_pnl(conn, a.id, market, row, now)
        if d is not None:
            pnl = (pnl or Decimal(0)) + d
    return {"nav": _s(nav), "day_pnl": _s(pnl), "accounts": ids}


def totals(
    conn: sqlite3.Connection,
    settings: Any,
    accounts: list[Account],
    *,
    now: datetime,
    paused: bool,
) -> dict:
    """`GET /accounts/overview`: KRW·USD 합계를 따로 두고, 환율이 있으면 원화 환산 합계를 더한다.
    파생(fo) 계좌는 통화·의미가 달라 합산에서 뺀다."""
    spot = [a for a in accounts if lane_of(settings, a) != "fo"]
    krw = _market_total(conn, spot, "KR", now)
    usd = _market_total(conn, spot, "US", now)
    fx = None
    quote = fx_rate(settings, conn, FX_PAIR)
    if quote is not None:
        rate = Decimal(str(quote["rate"]))
        total = Decimal(krw["nav"] or 0) + Decimal(usd["nav"] or 0) * rate
        fx = {
            "pair": quote.get("pair", FX_PAIR),
            "rate": str(rate),
            "as_of": quote.get("as_of"),
            "converted_total": str(total.quantize(Decimal(1), rounding=ROUND_HALF_UP)),
        }
    return {"krw": krw, "usd": usd, "fx": fx, "paused": paused, "at": now.isoformat()}
