from decimal import Decimal

from trader.kernel.broker import ticks
from trader.kernel.broker.models import FoProduct
from trader.kernel.broker.ticks import is_on_tick, quantize_price, tick_size

MINI = FoProduct(
    futures_prefix=("A05", "105"),
    call_prefix=("B05", "205"),
    put_prefix=("C05", "305"),
    multiplier=50000,
    futures_tick=Decimal("0.02"),
    option_ticks=[("3", "0.01"), ("10", "0.02"), ("inf", "0.05")],
    name="미니코스피200",
)
REG = FoProduct(
    futures_prefix=("A01", "101"),
    call_prefix=("B01", "201"),
    put_prefix=("C01", "301"),
    multiplier=250000,
    futures_tick=Decimal("0.05"),
    option_ticks=[("10", "0.01"), ("inf", "0.05")],
    name="코스피200",
)


def test_tick_table_single_source():
    assert ticks.tick_size("KR", Decimal("70000")) == Decimal("100")


def test_mini_option_tick_boundaries():
    t = ticks.tick_table_for(MINI, "CALL")
    assert ticks.tick_for(t, Decimal("2.99")) == Decimal("0.01")
    assert ticks.tick_for(t, Decimal("3.00")) == Decimal("0.02")
    assert ticks.tick_for(t, Decimal("9.98")) == Decimal("0.02")
    assert ticks.tick_for(t, Decimal("10.00")) == Decimal("0.05")
    assert ticks.tick_for(ticks.tick_table_for(MINI, "FUTURES"), Decimal("350")) == Decimal("0.02")
    assert ticks.tick_for(ticks.tick_table_for(REG, "PUT"), Decimal("9.99")) == Decimal("0.01")
    assert ticks.tick_for(ticks.tick_table_for(REG, "FUTURES"), Decimal("1")) == Decimal("0.05")


def test_quantize_crossing_boundary():
    t = ticks.tick_table_for(MINI, "CALL")
    assert ticks.quantize_tick(t, Decimal("2.996")) == Decimal("3.00")
    assert ticks.quantize_tick(t, Decimal("3.01")) == Decimal("3.02")
    assert ticks.quantize_tick(t, Decimal("9.99")) == Decimal("10.00")
    assert ticks.quantize_tick(t, Decimal("10.03")) == Decimal("10.05")
    f = ticks.tick_table_for(MINI, "FUTURES")
    assert ticks.quantize_tick(f, Decimal("350.03")) == Decimal("350.04")
    assert ticks.is_on_tick_table(f, Decimal("350.04"))
    assert not ticks.is_on_tick_table(f, Decimal("350.03"))


def test_kr_tick_table_boundaries():
    assert tick_size("KR", Decimal("999")) == Decimal("1")
    assert tick_size("KR", Decimal("4999")) == Decimal("5")
    assert tick_size("KR", Decimal("9999")) == Decimal("10")
    assert tick_size("KR", Decimal("49999")) == Decimal("50")
    assert tick_size("KR", Decimal("99999")) == Decimal("100")
    assert tick_size("KR", Decimal("499999")) == Decimal("500")
    assert tick_size("KR", Decimal("500000")) == Decimal("1000")


def test_us_tick_sub_dollar():
    assert quantize_price("US", Decimal("0.12341")) == Decimal("0.1234")
    assert is_on_tick("US", Decimal("12.345")) is False
