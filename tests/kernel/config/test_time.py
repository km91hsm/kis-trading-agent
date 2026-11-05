from typing import get_args

from trader.kernel.config import Env, Market
from trader.kernel.config.time import ET, KST, MARKET_TZ


def test_market_tz_maps_both_markets():
    assert MARKET_TZ == {"KR": KST, "US": ET}
    assert str(ET) == "America/New_York"


def test_env_and_market_literals():
    assert Env.__args__ == ("paper", "real")
    assert Market.__args__ == ("KR", "US")


def test_market_tz_keys_match_market_literal():
    assert set(MARKET_TZ) == set(get_args(Market))


def test_check_hm():
    import pytest

    from trader.kernel.config.time import HM_PATTERN, check_hm

    for ok in ("00:00", "09:05", "23:59"):
        assert check_hm(ok) == ok
        assert HM_PATTERN.match(ok)
    for bad in ("24:00", "9:05", "09:60", "0905", "09:05 ", "09:05\n", ""):
        with pytest.raises(ValueError, match="시각은 HH:MM 형식이어야 합니다"):
            check_hm(bad)
