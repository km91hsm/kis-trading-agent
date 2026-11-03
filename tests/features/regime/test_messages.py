"""regime 알림 문구 — 코드값을 한글로(옛 tests/notify/test_messages.py)."""

from trader.features.regime import messages as M


def test_regime_view_mode_messages_have_no_english_codes():
    assert M.regime_change(market="KR", before=None, after="bear", count="2/3") == (
        "[국면 전환] KR — → 하락 (2/3)"
    )
    assert M.regime_change(market="US", before="neutral", after="bull", count="0/3") == (
        "[국면 전환] US 중립 → 상승 (0/3)"
    )
    assert M.market_view(market="KR", stance="RISK_OFF", hedge_weight=0.2, leveraged=False) == (
        "[시장 뷰] KR 위험 회피 · 헤지 제안 20% (레버리지 거부)"
    )
