"""시장별 왕복 거래 비용(스펙 §6.1): KR 30bp, US 50bp. 레인·서비스 기능이 함께 쓴다."""

from __future__ import annotations

ROUND_TRIP_BP: dict[str, int] = {"KR": 30, "US": 50}


def round_trip_bp(market: str) -> int:
    """시장별 왕복 비용(bp). 모르는 시장은 보수적으로 US 값."""
    return ROUND_TRIP_BP.get(market, ROUND_TRIP_BP["US"])
