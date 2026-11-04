"""FAKE_CLAUDE=1 세션 공용 헬퍼: 호출 메타 모양·거부 예외·결과 검사·고정 리포트/토론 턴(기능별 fake
모듈이 쓴다)."""

from __future__ import annotations

from trader.kernel.session.calls import FAKE_MODEL

__all__ = [
    "FAKE_MODEL", "FakeSessionError", "ensure_ok", "fake_call", "fake_report", "fake_turn",
]  # fmt: skip


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


# 고정 응답(애널리스트 NEUTRAL, bull BULLISH 0.7 / bear BEARISH 0.6) — spot·fo 공용


def fake_report(ticker: str, asof: str, role: str) -> dict:
    return {
        "ticker": ticker,
        "asof": asof,
        "role": role,
        "summary": f"FAKE {role} 리포트",
        "bullets": ["FAKE 근거"],
        "signal": "NEUTRAL",
        "score": 0.0,
        "confidence": 0.5,
        "horizon": "short",
        "key_risks": [],
        "counter_evidence": [],
        "evidence": [{"claim": "FAKE", "source_id": f"{role}"}],
        "data_gaps": ["FAKE 모드"],
    }


def fake_turn(side: str, round_no: int) -> dict:
    bull = side == "bull"
    return {
        "side": side,
        "round": round_no,
        "stance": "BULLISH" if bull else "BEARISH",
        "valuation_view": "UNDERVALUED" if bull else "OVERVALUED",
        "arguments": [f"FAKE {side} 논거"],
        "rebuttals": [f"FAKE 반박 r{round_no}"] if round_no > 0 else [],
        "timing": {"action": "WAIT", "condition": None, "price_zone": None},
        "conviction": 0.7 if bull else 0.6,
        "what_would_change_my_mind": ["FAKE"],
    }
