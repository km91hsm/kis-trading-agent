"""regime 공개면: 다른 기능이 쓸 설정 모델·국면 조회·킬스위치/블랙아웃 사유·제외 종목."""

from typing import Any

from trader.features.regime.blackout import reason as blackout_reason
from trader.features.regime.killswitch import reason as killswitch_reason
from trader.features.regime.market_view import load_view as view_today
from trader.features.regime.regime import core_count, load
from trader.features.regime.settings import BlackoutCfg, HedgeCfg, HedgeInstrument
from trader.features.regime.settings import hedge_tickers as _hedge_tickers


def hedge_tickers(settings: Any, market: str) -> frozenset[str]:
    """그 시장의 헤지 상품 코드(헤지가 꺼져 있으면 빈 집합)."""
    return _hedge_tickers(settings.hedge, market)


def excluded_tickers(settings: Any, market: str) -> frozenset[str]:
    """스캔·분석에서 빼는 종목: 헤지 상품 ∪ (켜진 cash_park 의 국내 종목).
    `cash_park` 은 spot 설정 절이라 없을 수 있다(덕 타이핑)."""
    out = set(hedge_tickers(settings, market))
    park = getattr(settings, "cash_park", None)
    if park is not None and getattr(park, "enabled", False) and market == "KR":
        out.add(park.ticker)
    return frozenset(out)


__all__ = [
    "BlackoutCfg",
    "HedgeCfg",
    "HedgeInstrument",
    "blackout_reason",
    "core_count",
    "excluded_tickers",
    "hedge_tickers",
    "killswitch_reason",
    "load",
    "view_today",
]
