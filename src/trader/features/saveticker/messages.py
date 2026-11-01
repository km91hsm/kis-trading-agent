"""saveticker 알림 문구."""

from __future__ import annotations

from trader.kernel.notify import messages


def saveticker_health(text: str) -> str:
    """세이브티커 수집 장애·복구(수집기 감시가 만든 고정 문구 한 줄)."""
    return messages.event("세이브티커 수집", text)
