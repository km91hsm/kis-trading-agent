from decimal import Decimal

from trader.kernel.broker._parse import dec, rows, to_int


def test_broker_parse_helpers():
    assert dec("71,500".replace(",", "")) == Decimal("71500") and dec("") == Decimal("0")
    assert dec(None) == Decimal("0") and dec("x", default="1") == Decimal("1")
    assert to_int(" 3 ") == 3 and to_int(None) == 0
    assert rows([{"a": 1}, {}, "x", {"b": 2}]) == [{"a": 1}, {"b": 2}]
    assert rows({"a": 1}) == [{"a": 1}] and rows({}) == [] and rows(None) == []
