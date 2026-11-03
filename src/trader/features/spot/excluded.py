"""분석·monitor·종목 수·섹터 계산에서 뺄 운용 상품(옛 `Settings.excluded_tickers`)."""

from __future__ import annotations

from typing import Any

from trader.kernel.execution.ports import RegimePort


def excluded_tickers(settings: Any, regime: RegimePort | None, market: str) -> frozenset[str]:
    """헤지 ETF(regime 포트, 헤지 꺼져 있으면 없음) ∪ 현금 단기금리 ETF(`cash_park` 켜져 있고 KR).
    헤지 진입·해제가 볼 종목은 포트의 `hedge_tickers` 그대로. regime 포트가 없으면 헤지 없음."""
    out = set(regime.hedge_tickers(market)) if regime is not None else set()
    park = settings.cash_park
    if park.enabled and market == "KR":
        out.add(park.ticker)
    return frozenset(out)
