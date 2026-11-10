"""spot 운영 점검 영역: 체결 매칭 모호(warning) · 야간 대조 불일치(warning) · 대조 미실행(watch —
현지 평일, 예정 시각 + 30분 유예 뒤, 휴장·건너뜀이 아닐 때)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from apscheduler.triggers.cron import CronTrigger

from trader.kernel.config.market_hours import is_us_holiday
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.daemon.heartbeat import read_status
from trader.kernel.ledger.control import get_flag
from trader.kernel.ops.types import Item, OpsContext
from trader.kernel.registry.types import HealthArea
from trader.kernel.session.run_store import run_date_for

RECONCILE_GRACE = timedelta(minutes=30)
_DEFAULT_DUE = time(16, 40)  # 대조 잡이 없을 때의 현지 예정 시각


def _job_id(market: str) -> str:
    return f"{market.lower()}_reconcile"


def reconcile_due(market: str, local_day: date, jobs: Sequence[Any], now: datetime) -> datetime:
    """그 시장 현지 날짜의 대조 예정 시각(거래소 시간대). 잡 `{m}_reconcile` 의 cron 을 그 시간대
    그대로 써 현지 자정 뒤 첫 발화를 거래소 시각으로 바꾼다(KST 06:40 → 그날 ET 17:40). 잡이 없으면
    현지 16:40."""
    tz = MARKET_TZ[market]
    start = datetime.combine(local_day, time(0, 0), tzinfo=tz)
    for j in jobs:
        if getattr(j, "id", None) == _job_id(market) and getattr(j, "cron", None):
            fire = CronTrigger(**j.trigger_kwargs).get_next_fire_time(None, start)
            if fire is not None:
                return fire.astimezone(tz)
    return datetime.combine(local_day, _DEFAULT_DUE, tzinfo=tz)


def reconcile_skipped_today(
    state: Path, market: str, local_day: date, holiday_fn: Callable[[date], bool] | None
) -> bool:
    """오늘 대조가 없어도 되는가: 휴장(holiday_fn — 없으면 US 만 NYSE 규칙) 이거나 하트비트상
    `{m}_reconcile` 이 그 현지 날짜에 `skipped` 로 끝났다(KR 휴장·일시정지 등 잡의 판단)."""
    if holiday_fn is None:
        holiday_fn = is_us_holiday if market == "US" else None
    if holiday_fn is not None and holiday_fn(local_day):
        return True
    probe = SimpleNamespace(id=_job_id(market), kind="reconcile", market=market, cron=None,
                            interval_s=None)  # fmt: skip
    now = datetime.combine(local_day, time(0, 0), tzinfo=MARKET_TZ[market])
    for j in read_status(state, now, [probe])["jobs"]:
        end = j.get("last_end")
        if j.get("last_status") == "skipped" and end:
            if datetime.fromisoformat(end).astimezone(MARKET_TZ[market]).date() == local_day:
                return True
    return False


def _holiday_fn(ctx: OpsContext, market: str) -> Callable[[date], bool] | None:
    """accounts 포트의 `(market, d)` 휴장 판정을 시장 하나로 묶는다(KR 은 KIS 조회 — 필요할
    때만 부른다)."""
    make = getattr(ctx.ports.get("accounts"), "holiday_fn", None)
    if make is None:
        return None
    fn = make()
    if fn is None:  # 커널 Protocol 이 None 을 허용 — reconcile_skipped_today 폴백
        return None
    return lambda d: bool(fn(market, d))


def check(ctx: OpsContext) -> list[Item]:
    out: list[Item] = []
    port = ctx.ports.get("accounts")
    accounts = port.accounts() if port is not None else []
    accounts = [a for a in accounts if getattr(a, "asset", "stock") == "stock"]
    for m in sorted({mm for a in accounts for mm in a.markets}):
        amb = get_flag(ctx.conn, f"fill_ambiguous:{m}:{run_date_for(m, ctx.now).isoformat()}")
        if amb:
            out.append(
                Item(
                    f"spot.fill_ambiguous.{m}",
                    "warning",
                    f"{m} 체결 매칭 모호 — unknown 주문 후보가 둘 이상, 사용자 확인 필요",
                    link="/#orders-timeline",
                    extra=amb if isinstance(amb, dict) else {},
                )
            )
    for a in accounts:
        for m in a.markets:
            d = run_date_for(m, ctx.now)
            flag = get_flag(ctx.conn, f"reconcile:{a.id}:{m}:{d.isoformat()}")
            local = ctx.now.astimezone(MARKET_TZ[m])
            if flag:
                if isinstance(flag, dict) and not flag.get("ok", True):
                    n = len(flag.get("mismatches") or []) + len(flag.get("open_orders") or [])
                    out.append(
                        Item(
                            f"spot.reconcile.{a.id}.{m}",
                            "warning",
                            f"{a.id} {m} 야간 대조 불일치 {n}건 (스냅샷은 브로커 값으로 맞춤)",
                            link=f"/accounts/{a.id}",
                            count=n,
                        )
                    )
                continue
            if local.weekday() >= 5:
                continue
            due = reconcile_due(m, local.date(), ctx.jobs, ctx.now) + RECONCILE_GRACE
            if local < due:
                continue
            if reconcile_skipped_today(ctx.state, m, local.date(), _holiday_fn(ctx, m)):
                continue
            out.append(
                Item(
                    f"spot.reconcile.{a.id}.{m}",
                    "watch",
                    f"{a.id} {m} 오늘 야간 대조가 아직 돌지 않음",
                    link="/system#daemon",
                )
            )
    return out


AREA = HealthArea("spot", check)
