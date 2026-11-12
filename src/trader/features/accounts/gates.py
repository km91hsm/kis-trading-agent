"""accounts 가 데몬에 공급하는 게이트: market(일시정지·주말·휴장), monitor(주말·휴장 + 장중 창).

monitor 게이트는 일시정지를 보지 않는다(Ruling 20) — 일시정지 중에도 장중 감시는 돌아 손절선
도달 시 손절(일시정지 중에도 실행, `stop_while_paused` 알림 하루 1회)·트리거 기록을 하고,
재평가는 `monitor_once` 가 막는다."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from trader.features.accounts import api
from trader.kernel.config.market_hours import close_hm, in_window, us_early_closes
from trader.kernel.config.time import KST, MARKET_TZ
from trader.kernel.daemon.gates import should_skip
from trader.kernel.registry.types import GateSpec
from trader.kernel.session.run_store import run_date_for


def _build_day_gate(
    boot: Any, deps: Any, *, check_paused: bool
) -> Callable[[Any], str | None]:
    """잡의 시장이 오늘 쉬는지(주말·휴장, `check_paused` 면 일시정지도) 판정한다."""
    port = deps.ports["accounts"]
    holiday = port.holiday_fn()

    def gate(job: Any) -> str | None:
        market = job.market
        conn = deps.conn_factory()
        try:
            paused = (lambda: api.is_paused(conn)) if check_paused else (lambda: False)
            if market is None:
                return "일시정지" if paused() else None
            today = run_date_for(market, deps.now())
            return should_skip(
                market, today, paused=paused, is_holiday=lambda d: holiday(market, d)
            )
        finally:
            conn.close()

    return gate


def build_market_gate(boot: Any, deps: Any) -> Callable[[Any], str | None]:
    """잡의 시장이 오늘 쉬는지(일시정지·주말·휴장) 판정한다. 시장 없는 잡은 일시정지만 본다."""
    return _build_day_gate(boot, deps, check_paused=True)


def build_session_gate(boot: Any, deps: Any) -> Callable[[Any], str | None]:
    """주말·휴장만 본다(일시정지는 보지 않음) — 주문 없이 조회만 하는 잡(야간 대사)용. 일시정지
    중에도 위험 청산은 나가므로 그동안의 원장·잔고 대조를 거르지 않는다."""
    return _build_day_gate(boot, deps, check_paused=False)


def build_monitor_gate(boot: Any, deps: Any) -> Callable[[Any], str | None]:
    """주말·휴장(일시정지는 보지 않음) + 시장 현지 시각이 schedule `<시장>.monitor.start/end` 창
    안인지."""
    day_gate = _build_day_gate(boot, deps, check_paused=False)

    def gate(job: Any) -> str | None:
        reason = day_gate(job)
        if reason or job.market is None:
            return reason
        window = getattr(deps.schedule, job.market.lower()).monitor
        local = deps.now().astimezone(MARKET_TZ.get(job.market, KST))
        # 조기 마감일(US 13:00)은 창 끝을 그날 마감으로 당긴다 — 장 밖 손절 주문이 거부되며 반복됨
        end = window.end
        if job.market == "US" and local.date() in us_early_closes(local.year):
            end = min(end, close_hm(job.market, local.date()))
        return None if in_window(local.time(), window.start, end) else "창 밖"

    return gate


GATES = (
    GateSpec("market", build_market_gate),
    GateSpec("monitor", build_monitor_gate),
    GateSpec("session", build_session_gate),
)
