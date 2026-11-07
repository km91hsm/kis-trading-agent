"""다른 기능에 제공하는 저장 자료 조회 포트. 브로커를 호출하지 않는다."""

from trader.features.quotes.market_activity import snapshot
from trader.kernel.registry.types import Port


class MarketActivityPort:
    snapshot = staticmethod(snapshot)


PORT = Port("market_activity", lambda boot: MarketActivityPort())
