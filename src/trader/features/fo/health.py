"""fo 운영 점검 영역: unknown·exit_failed 포지션(critical) · 정산 대기가 최종거래일 뒤 1일 초과
(warning) · 옵션 매도 최종거래일 D-1 미청산(critical) · 파생 마감(15:45) 뒤 살아 있는 주문(watch).
파생 계좌가 없으면 아무것도 보지 않는다."""

from __future__ import annotations

from datetime import date, timedelta

from trader.kernel.config.market_hours import fo_close_hm
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.execution.ledger import day_bounds
from trader.kernel.execution.types import dead_status_sql
from trader.kernel.ops.types import Item, OpsContext
from trader.kernel.registry.types import HealthArea
from trader.kernel.session.run_store import run_date_for

FO_SETTLE_GRACE_DAYS = 1
_LINK = "/system#fo"


def _fo_accounts(ctx: OpsContext) -> list:
    port = ctx.ports.get("accounts")
    if port is None:
        return []
    accts = port.fo_accounts() if hasattr(port, "fo_accounts") else port.accounts()
    return [a for a in accts if getattr(a, "asset", "stock") == "fo"]


def check(ctx: OpsContext) -> list[Item]:
    out: list[Item] = []
    ids = [a.id for a in _fo_accounts(ctx)]
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    rows = ctx.conn.execute(
        "SELECT id, status, vehicle, last_trade_date FROM fo_positions "
        f"WHERE account_id IN ({marks}) AND status NOT IN ('closed','settled') ORDER BY id",
        ids,
    ).fetchall()
    today = run_date_for("KR", ctx.now)
    bad = [r for r in rows if r["status"] in ("unknown", "exit_failed")]
    if bad:
        out.append(
            Item(
                "fo.position_blocked",
                "critical",
                f"파생 포지션 unknown·exit_failed {len(bad)}건 — 사람이 adopt·close 로 정리",
                link=_LINK,
                count=len(bad),
                extra={"ids": [int(r["id"]) for r in bad]},
            )
        )
    settling = [
        r
        for r in rows
        if r["status"] == "awaiting_settlement"
        and r["last_trade_date"]
        and (today - date.fromisoformat(r["last_trade_date"])).days > FO_SETTLE_GRACE_DAYS
    ]
    if settling:
        out.append(
            Item(
                "fo.settlement_overdue",
                "warning",
                f"파생 정산 대기 {len(settling)}건이 최종거래일 뒤 "
                f"{FO_SETTLE_GRACE_DAYS}운영일을 넘김",
                link=_LINK,
                count=len(settling),
            )
        )
    sell_near = [
        r
        for r in rows
        if r["vehicle"] == "OPTION_SELL"
        and r["status"] in ("held", "resizing", "entering")
        and r["last_trade_date"]
        and (date.fromisoformat(r["last_trade_date"]) - today).days <= 1
    ]
    if sell_near:
        out.append(
            Item(
                "fo.option_sell_expiry",
                "critical",
                f"옵션 매도 {len(sell_near)}건이 최종거래일 D-1 안인데 미청산",
                link=_LINK,
                count=len(sell_near),
            )
        )
    close = fo_close_hm(False)
    closed = ctx.now.astimezone(MARKET_TZ["KR"]).strftime("%H:%M") >= close
    bound = day_bounds(today + timedelta(days=1) if closed else today, "KR")[0]
    stale = int(
        ctx.conn.execute(
            f"SELECT COUNT(*) FROM orders WHERE asset='fo' AND account_id IN ({marks}) "
            f"AND created_at < ? AND status NOT IN ({dead_status_sql()},'filled')",
            [*ids, bound],
        ).fetchone()[0]
    )
    if stale:
        out.append(
            Item(
                "fo.orders_stale",
                "watch",
                f"파생 마감({close}) 뒤에도 정리되지 않은 주문 {stale}건 (체결 동기화·대조 확인)",
                link="/#orders-timeline",
                count=stale,
            )
        )
    return out


AREA = HealthArea("fo", check)
