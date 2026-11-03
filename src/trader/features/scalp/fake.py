"""FAKE_CLAUDE=1 고정 응답: 진입하지 않고 보유 유지."""

FAKE_VERDICT = {
    "go": False,
    "size": "half",
    "take_profit_pct": 0.01,
    "stop_pct": 0.005,
    "max_hold_min": 30,
    "reason": "FAKE 모드: 진입하지 않음",
    "confidence": 0.5,
}
FAKE_ADVICE = {"action": "hold", "reason": "FAKE 모드", "confidence": 0.5}
