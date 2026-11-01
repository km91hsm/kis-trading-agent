from trader.kernel.config import Env, Market
from trader.kernel.config.time import ET, KST, MARKET_TZ


def test_market_tz_maps_both_markets():
    assert MARKET_TZ == {"KR": KST, "US": ET}
    assert str(ET) == "America/New_York"


def test_env_and_market_literals():
    assert Env.__args__ == ("paper", "real")
    assert Market.__args__ == ("KR", "US")
