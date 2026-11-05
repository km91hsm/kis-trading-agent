"""regime 운영 점검 영역: 킬스위치 켜짐(critical) · 입출금 의심(warning, 기록 뒤 2운영일) ·
현물 계좌·시장별 최근 5거래일 실현손익(NAV 대비 −2% watch · −5% warning · −10% critical)."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from trader.features.regime import killswitch
from trader.kernel.execution.ledger import latest_nav_before, realized_pnl_5d
from trader.kernel.ops.types import Item, OpsContext
from trader.kernel.registry.types import HealthArea
from trader.kernel.session.run_store import run_date_for

logger = logging.getLogger("trader.regime")

LOSS_WATCH_5D = Decimal("-0.02")
LOSS_WARNING_5D = Decimal("-0.05")
LOSS_CRITICAL_5D = Decimal("-0.10")
FLOW_SHOW_DAYS = 2  # 입출금 의심(2-O16)을 기록 뒤 며칠(운영일) 보일지
_RELEASE = "`trader regime killswitch off --note`"


def _loss_level(pct: Decimal) -> str | None:
    if pct <= LOSS_CRITICAL_5D:
        return "critical"
    if pct <= LOSS_WARNING_5D:
        return "warning"
    if pct <= LOSS_WATCH_5D:
        return "watch"
    return None


def _flow_date(flow: dict) -> date | None:
    """입출금 의심 기록의 날짜. 깨진 값이면 그 항목만 건너뛴다(영역 전체가 오류가 되지 않게, T3)."""
    try:
        return date.fromisoformat(str(flow.get("date")))
    except ValueError:
        logger.warning("입출금 의심 기록의 날짜가 잘못됨: %r", flow.get("date"))
        return None


def check(ctx: OpsContext) -> list[Item]:
    out: list[Item] = []
    ks = killswitch.reason(ctx.conn)
    if ks:
        text = f"킬스위치 켜짐 — 신규 매수 중단: {ks}. 해제는 {_RELEASE} (원인 메모)"
        out.append(Item("regime.killswitch", "critical", text, link="/system"))
    port = ctx.ports.get("accounts")
    accounts = port.accounts() if port is not None else []
    accounts = [a for a in accounts if getattr(a, "asset", "stock") == "stock"]
    for a in accounts:
        for m in a.markets:
            d = run_date_for(m, ctx.now)
            link = f"/accounts/{a.id}"
            flow = killswitch.flow_suspicion(ctx.conn, a.id, m)
            flow_date = _flow_date(flow) if flow else None
            if flow_date is not None and (d - flow_date).days <= FLOW_SHOW_DAYS:
                out.append(
                    Item(
                        f"regime.flow.{a.id}.{m}",
                        "warning",
                        f"{a.id} {m} 입출금 의심 — 가격으로 설명되지 않는 NAV 변화 "
                        f"{flow.get('residual')} ({flow.get('date')}). 킬스위치 기준이 그대로라 "
                        f"출금은 손실로, 입금은 손실 은폐로 보입니다. 확인 뒤 {_RELEASE} 로 "
                        "기준을 다시 잡으세요(모든 계좌·시장의 기준을 최근 NAV 로 다시 잡습니다)",
                        link=link,
                    )
                )
            nav = latest_nav_before(ctx.conn, a.id, d + timedelta(days=1), market=m)
            if not nav or nav <= 0:
                continue
            pct = realized_pnl_5d(ctx.conn, a.id, d, market=m) / nav
            level = _loss_level(pct)
            if level is None:
                continue
            out.append(
                Item(
                    f"regime.loss.{a.id}.{m}",
                    level,
                    f"{a.id} {m} 최근 5거래일 실현손익 {float(pct):+.2%} (NAV 대비)",
                    link=link,
                    extra={"pct": float(pct)},
                )
            )
    return out


AREA = HealthArea("regime", check)
