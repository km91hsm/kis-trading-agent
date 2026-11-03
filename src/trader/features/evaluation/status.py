"""`/status` 의 「성과」 절: 계좌×시장별 최근 30일 수익률·MDD (RL 줄은 rl 쪽이 덧붙인다)."""

from __future__ import annotations

from typing import Any

from trader.features.accounts.api import load_accounts, split_accounts
from trader.features.evaluation.performance import metrics_of, nav_series
from trader.kernel.registry.types import StatusSection


def render(ctx: Any) -> list[str]:
    accounts, _ = split_accounts(load_accounts())
    out = []
    for a in accounts:
        for m in a.markets:
            met = metrics_of(nav_series(ctx.conn, a.id, m, days=30))
            if met["days"] == 0:
                continue
            ret, mdd = met["return"], met["mdd"]
            ret_s = "—" if ret is None else f"{ret:+.2%}"
            mdd_s = "—" if mdd is None else f"{mdd:+.2%}"
            out.append(f"- {a.id} {m}: 30일 {ret_s} · MDD {mdd_s}")
    return out


STATUS_SECTION = StatusSection("성과", render)
