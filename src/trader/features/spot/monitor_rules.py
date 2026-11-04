"""장중 모니터의 순수 규칙(스펙 §5.2) — 입력 → `Signal` 목록. DB·브로커·포트를 모른다.

- 손절선: 결정의 stop_loss(또는 보유 중 최고 손절 바닥), 없으면 평단 × (1 + 기본 손절 비율).
- 트리거: take_profit 도달, 프로필 임계의 급등락(평단 대비), `invalidation_rules`(구조화 조건, L16 —
  문자열 `invalidation_conditions` 는 설명용이라 자동으로 읽지 않는다), 단기 정기 재평가.
- 시세·종가표·트리거 이력을 읽고 주문을 내는 `monitor_once` 는 호출자(monitor)의 몫이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from trader.features.spot import horizon
from trader.features.spot.settings import HorizonProfile
from trader.features.spot.stops import effective_stop
from trader.kernel.broker.models import Position
from trader.kernel.execution.monitor_rules import rule_hit

MOVE_PCT = Decimal("0.07")  # 프로필이 없을 때(mid) 기본 — 정본은 horizon_profiles


@dataclass
class Signal:
    kind: str  # stop_loss | take_profit | move_7pct | invalidation | periodic
    price: Decimal
    detail: dict = field(default_factory=dict)


def _rule_text(rule: dict) -> str:
    days = int(rule.get("days") or 1)
    tail = f" ({days}일)" if rule.get("metric") == "close" and days > 1 else ""
    return f"{rule.get('metric')} {rule.get('op')} {rule.get('value'):g}{tail}"


def _q2(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def needs_closes(dec: dict | None) -> bool:
    """`close` 무효화 조건이 있는 결정만 완성 일봉 종가가 필요하다(읽을지는 호출자가 정한다)."""
    rules = (dec or {}).get("invalidation_rules") or []
    return any(r.get("metric") == "close" for r in rules)


def closes_from_column(column: Mapping[date, Any]) -> list[Decimal | None]:
    """종가표의 한 종목 열(거래일 → 종가)을 날짜순 종가 목록으로. 거래일(표의 모든 날짜) 정렬을
    지킨다 — 그 종목만 빠진 날·NaN 은 None 으로 자리를 남긴다(지우면 연속이 아닌 종가가 연속처럼
    보인다)."""
    return [
        None if v is None or v != v else Decimal(str(v))  # NaN != NaN
        for _, v in sorted(column.items(), key=lambda kv: kv[0])
    ]


def monotone_stop(data: dict | None, best_floor: Decimal | None) -> dict | None:
    """손절선을 보유 기간 중 결정들의 최고값(`best_floor`)으로 단조 유지한다(전체 검토 L5). 최신
    결정이 손절가를 비웠거나(보유 재분석 HOLD) 더 낮게 냈어도 이전 손절선이 살아 있다 — 기본
    −15% 로 풀리지 않게. 바닥 조회(DB)는 호출자 몫이다."""
    _, source = effective_stop((data or {}).get("stop_loss"), best_floor, None, 0)
    if source != "floor":
        return data
    return {**(data or {}), "stop_loss": float(best_floor)}


def check_position(
    pos: Position,
    dec_data: dict | None,
    quote: Decimal,
    *,
    stop_loss_pct: float,
    now: datetime,
    move_pct: Decimal | None = None,
    closes: list[Decimal | None] | None = None,
) -> list[Signal]:
    """순수 함수. 손절이면 손절 신호만, 아니면 take_profit → move_7pct → invalidation 순.
    `move_pct` 는 보유기간 프로필의 급등락 임계(Phase 23 T, 없으면 7%). `closes` 는 `close`
    무효화 조건용 완성 일봉 종가(없으면 그 조건은 미판정). `now` 는 호출 규약용으로 받으며
    현재 판정에는 쓰이지 않는다 — 휴장·세션 정렬은 종가 목록을 만드는 쪽에서 이미 반영한다."""
    dec = dec_data or {}
    threshold = move_pct if move_pct is not None else MOVE_PCT
    level, _ = effective_stop(dec.get("stop_loss"), None, pos.avg_price, stop_loss_pct)
    if level is not None and quote <= level:
        return [Signal("stop_loss", quote, {"level": _q2(level), "avg_price": str(pos.avg_price)})]
    out: list[Signal] = []
    tp = dec.get("take_profit")
    if tp and quote >= Decimal(str(tp)):
        out.append(Signal("take_profit", quote, {"level": str(tp)}))
    if pos.avg_price > 0:
        move = quote / pos.avg_price - 1
        if abs(move) >= threshold:
            out.append(
                Signal(
                    "move_7pct",
                    quote,
                    {"move": _q2(move * 100) + "%", "threshold": _q2(threshold * 100) + "%"},
                )
            )
    for rule in dec.get("invalidation_rules") or []:
        if rule_hit(rule, quote, closes):
            out.append(Signal("invalidation", quote, {"rule": _rule_text(rule)}))
            break
    return out


def periodic_signal(
    quote: Decimal,
    ref_price: Decimal,
    *,
    profile: HorizonProfile,
    last_periodic_at: datetime | None,
    count_today: int,
    now: datetime,
) -> Signal | None:
    """단기 보유의 시간 단위 정기 재평가(Phase 23 T, 순수 함수). `periodic_check_min` 이 0 이면
    없음. 직전 정기 점검(없으면 결정 시각 기준 — 호출자가 None 을 넘기면 바로 가능)에서 간격이
    지났고, 기준가 대비 변동이 `move_pct/2` 이상이며, 운영일 상한 안일 때만."""
    if profile.periodic_check_min <= 0 or count_today >= horizon.PERIODIC_DAILY_CAP:
        return None
    if last_periodic_at is not None and now - last_periodic_at < timedelta(
        minutes=profile.periodic_check_min
    ):
        return None
    if ref_price <= 0:
        return None
    move = quote / ref_price - 1
    if abs(move) < Decimal(str(profile.move_pct)) / 2:
        return None
    return Signal(
        "periodic",
        quote,
        {"move": _q2(move * 100) + "%", "ref_price": str(ref_price), "profile": "short"},
    )
