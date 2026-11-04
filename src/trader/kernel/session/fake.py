"""FAKE_CLAUDE=1 세션 공용 헬퍼: 호출 메타 모양·거부 예외·결과 검사(기능별 fake 모듈이 쓴다)."""

from __future__ import annotations

from trader.kernel.session.calls import FAKE_MODEL

__all__ = ["FAKE_MODEL", "FakeSessionError", "ensure_ok", "fake_call"]


class FakeSessionError(RuntimeError):
    """FAKE 세션 도구 호출 거부(커널 러너가 error 결과로 닫는다)."""


def fake_call(agent: str) -> dict:
    """도구에 넘기는 `call_meta`(fake 모델·비용 0)."""
    return {
        "agent": agent,
        "model": FAKE_MODEL,
        "session_id": "fake",
        "cost_usd": 0.0,
        "duration_ms": 0,
    }


def ensure_ok(r: dict) -> None:
    """도구 결과가 ok 가 아니면 `FakeSessionError`(메시지 = r["error"], 없으면 결과 전체)."""
    if not r.get("ok"):
        raise FakeSessionError(r.get("error") or r)
