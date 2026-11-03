"""evaluation 텔레그램 명령: `/daily` 오늘 NAV·주문 일일 리포트."""

from __future__ import annotations

from typing import Any

from trader.features.accounts.api import load_accounts, split_accounts
from trader.features.evaluation import daily_report
from trader.kernel.notify import messages
from trader.kernel.registry.types import TelegramCommand
from trader.kernel.session.run_store import run_date_for


def handle_daily(ctx: Any, arg: str | None) -> str:
    accounts, _ = split_accounts(load_accounts())
    r = daily_report.compute(
        ctx.conn,
        accounts,
        markets=sorted({m for a in accounts for m in a.markets}),
        today_of=lambda m: run_date_for(m, ctx.now()),
    )
    return (
        daily_report.message(r)
        if r["items"]
        else messages.event("일일 리포트", "오늘 잔고 스냅샷이 아직 없어요")
    )


DAILY = TelegramCommand("daily", False, "/daily — 오늘 NAV·주문 일일 리포트", handle_daily)
