"""런타임 관심종목(옛 `tests/control/test_control.py` 의 watchlist 2건)."""

import pytest

from trader.features.scan import watchlist


def test_watchlist_market_filter(conn):
    watchlist.set_watchlist(conn, ["005930", "aapl", "US:BRK.B", "kr:000660"])
    assert watchlist.get_watchlist(conn, market="KR") == ["005930", "000660"]
    assert watchlist.get_watchlist(conn, market="US") == ["AAPL", "BRK.B"]
    assert watchlist.get_watchlist(conn) == ["005930", "AAPL", "US:BRK.B", "KR:000660"]
    with pytest.raises(ValueError):
        watchlist.set_watchlist(conn, ["JP:7203"])


def test_watchlist_canonical_and_remove(conn):
    assert watchlist.set_watchlist(conn, ["005930", "KR:005930", "aapl", "US:AAPL"]) == [
        "005930",
        "AAPL",
    ]
    assert watchlist.remove_watch(conn, "005930") == ["AAPL"]
    watchlist.set_watchlist(conn, ["KR:000660"])
    assert watchlist.remove_watch(conn, "000660") == []  # 접두사 없는 요청으로 접두사 항목 제거
