from datetime import date
from decimal import Decimal

import pytest

from trader.kernel.broker.errors import KisApiError
from trader.kernel.broker.fake import FakeBroker, FakeMarket
from trader.kernel.broker.models import Candle, Cash, OrderRequest, Position

from .conftest import make_account


def _kr(side="BUY", order_type="LIMIT", price=Decimal("71000"), qty=1):
    return OrderRequest(
        ticker="005930",
        market="KR",
        side=side,
        qty=qty,
        order_type=order_type,
        price=price,
        exchange=None,
    )


def _candles(n):
    return [
        Candle(date=date(2026, 1, 1 + i), open=1, high=2, low=1, close=1 + i, volume=10)
        for i in range(n)
    ]


def test_fake_market_same_interface():
    m = FakeMarket(
        prices={"005930": Decimal("71500"), "AAPL": Decimal("227.55")},
        candles={"005930": _candles(3)},
    )
    assert m.get_price_kr("005930").price == Decimal("71500")
    assert m.get_price_us("AAPL", "NAS").market == "US"
    assert [c.close for c in m.get_daily_kr("005930", days=2)] == [Decimal(2), Decimal(3)]
    assert m.get_daily_us("AAPL", "NAS") == []
    assert m.is_holiday(date(2026, 10, 10)) is False
    assert m.calls == ["get_price_kr", "get_price_us", "get_daily_kr", "get_daily_us", "is_holiday"]


def test_fake_fail_injection():
    m = FakeMarket(prices={}, fail={"get_price_kr"})
    with pytest.raises(KisApiError):
        m.get_price_kr("005930")


def test_fake_broker():
    acc = make_account("paper-main", "paper", ["KR", "US"])
    pos = [
        Position(
            ticker="005930",
            market="KR",
            qty=1,
            avg_price=1,
            current_price=1,
            eval_amount=1,
            pnl_pct=0.0,
        )
    ]
    b = FakeBroker(acc, positions=pos, cash={"KR": Cash(currency="KRW", available=1, total=1)})
    assert b.get_positions("KR") == pos and b.get_positions("US") == []
    assert b.get_cash("KR").currency == "KRW"
    with pytest.raises(KisApiError):
        b.get_cash("US")  # 설정 안 된 시장은 실패


def test_fake_market_minutes():
    one = Decimal(1)
    c = [
        Candle(
            date=date(2026, 10, 8),
            open=one,
            high=one,
            low=one,
            close=one,
            volume=1,
            time=f"09{i:02d}00",
        )
        for i in range(5)
    ]
    fm = FakeMarket(minutes={"005930": c})
    assert [x.time for x in fm.get_minute_kr("005930", count=2)] == ["090300", "090400"]
    assert fm.get_minute_us("AAPL", "NAS") == [] and fm.calls == ["get_minute_kr", "get_minute_us"]


def test_fake_broker_get_balance():
    a = make_account("paper-main", "paper")
    cash = {"KR": Cash(currency="KRW", available=Decimal(1), total=Decimal(1))}
    fb = FakeBroker(a, cash=cash)
    assert fb.get_balance("KR") == ([], cash["KR"])


def test_fake_broker_records():
    b = FakeBroker(make_account("paper-main", "paper"))
    r1 = b.place_order(_kr())
    r2 = b.place_order(_kr(side="SELL"))
    assert (r1.order_no, r2.order_no) == ("FAKE-1", "FAKE-2") and r1.accepted
    assert [o.side for o in b.orders] == ["BUY", "SELL"]
    assert b.cancel_order("FAKE-1", market="KR").accepted and b.cancelled == ["FAKE-1"]
    b2 = FakeBroker(make_account("paper-main", "paper"), fail={"place_order"})
    with pytest.raises(KisApiError):
        b2.place_order(_kr())
