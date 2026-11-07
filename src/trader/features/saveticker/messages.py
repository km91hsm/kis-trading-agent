"""saveticker 알림 문구."""

from __future__ import annotations

from trader.kernel.notify import messages

EXTENSION_RELOAD_NOTE = (
    "앱 업데이트로 수집 확장이 바뀌었습니다 — Chrome 확장 프로그램 화면(chrome://extensions)에서 "
    "Trader 수집 확장을 새로고침하세요"
)


def saveticker_health(text: str) -> str:
    """세이브티커 수집 장애·복구(수집기 감시가 만든 고정 문구 한 줄)."""
    return messages.event("세이브티커 수집", text)
