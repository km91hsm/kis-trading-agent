"""시장 게이트의 순수 판정: 일시정지·주말·휴장. 계좌·브로커는 모르고 호출자가 콜러블로 준다."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date


def should_skip(
    market: str,
    today: date,
    *,
    paused: Callable[[], bool],
    is_holiday: Callable[[date], bool],
) -> str | None:
    """건너뛸 사유("일시정지"/"주말"/"휴장일") 또는 None. today 는 이미 시장 현지 운영일이다.
    휴장 조회 실패를 개장으로 보는 정책은 is_holiday 공급자(accounts holiday_fn)의 몫이다."""
    if paused():
        return "일시정지"
    if today.weekday() >= 5:
        return "주말"
    if is_holiday(today):
        return "휴장일"
    return None
