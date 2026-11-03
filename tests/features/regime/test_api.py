"""regime 공개면: excluded_tickers = 헤지 상품 ∪ (켜진 cash_park 의 KR 종목)."""

from types import SimpleNamespace

from trader.features.regime import api
from trader.features.regime.settings import HedgeCfg


def _s(settings, *, hedge_enabled=True, park=None):
    s = settings.model_copy(update={"hedge": HedgeCfg(enabled=hedge_enabled)})
    object.__setattr__(s, "cash_park", park)
    return s


def test_excluded_tickers_union_hedge_and_cash_park(settings):
    park = SimpleNamespace(enabled=True, ticker="357870")
    hedge = api.hedge_tickers(_s(settings), "KR")
    assert hedge == frozenset({"114800", "252670"})
    assert api.excluded_tickers(_s(settings, park=park), "KR") == hedge | {"357870"}
    assert api.excluded_tickers(_s(settings, park=park), "US") == api.hedge_tickers(
        _s(settings), "US"
    )
    off = SimpleNamespace(enabled=False, ticker="357870")
    assert api.excluded_tickers(_s(settings, park=off), "KR") == hedge
    assert api.excluded_tickers(_s(settings), "KR") == hedge
    assert api.excluded_tickers(_s(settings, hedge_enabled=False, park=park), "KR") == {"357870"}
    assert api.excluded_tickers(_s(settings, hedge_enabled=False), "KR") == frozenset()
    assert callable(api.view_today)
