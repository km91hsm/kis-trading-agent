"""FAKE_CLAUDE=1 복기 세션: claude 를 띄우지 않고 reflector 의 도구 호출을 흉내 낸다.
실제 세션과 같은 도구 함수(`submit_reflection`)를 쓰므로 스키마 검증이 그대로 적용된다."""

from __future__ import annotations

from typing import Any

from trader.features.evaluation import tools
from trader.kernel.session.fake import fake_call


def run_fake_reflect(lane_ctx: Any, spec: Any) -> list[str]:
    """reflect 모드: 모든 엔트리에 NEUTRAL 반성(lesson 없음)."""
    entries = spec.extra.get("entries") or []
    if not entries:
        return []
    payload = [
        {
            "entry_id": e["entry_id"],
            "signal_review": {
                role: {"verdict": "UNKNOWN", "note": "FAKE"}
                for role in (e.get("analyst_signals") or {})
            },
            "decision_review": {"verdict": "NEUTRAL", "note": "FAKE 모드: 판단 보류"},
            "lesson": None,
            "confidence": 0.5,
        }
        for e in entries
    ]
    r = tools.submit_reflection(
        lane_ctx,
        market=spec.market,
        date=spec.date,
        payload=payload,
        call_meta=fake_call("reflector"),
    )
    if not r["ok"]:
        raise RuntimeError(r["error"])
    return ["_reflect"]
