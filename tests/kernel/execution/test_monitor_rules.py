"""kernel/execution/monitor_rules.rule_hit — 구조화 무효화 조건 한 개 판정."""

from decimal import Decimal

from trader.kernel.execution.monitor_rules import rule_hit


def test_price_rule():
    rule = {"metric": "price", "op": "<", "value": 100}
    assert rule_hit(rule, Decimal("99"), None) is True
    assert rule_hit(rule, Decimal("100"), None) is False


def test_close_rule_needs_enough_complete_closes():
    two = {"metric": "close", "op": "<", "value": 100, "days": 2}
    d = Decimal
    assert rule_hit(two, d("90"), None) is None
    assert rule_hit(two, d("90"), [d("90")]) is None  # 종가 부족
    assert rule_hit(two, d("90"), [d("90"), None]) is None  # 빠진 거래일
    assert rule_hit(two, d("90"), [None, d("90"), d("95")]) is True
    assert rule_hit(two, d("90"), [d("90"), d("101")]) is False


def test_invalid_rule_is_none():
    d = Decimal
    assert rule_hit({"metric": "price", "op": "!=", "value": 1}, d("1"), None) is None
    assert rule_hit({"metric": "price", "op": "<", "value": "x"}, d("1"), None) is None
    assert rule_hit({"metric": "price", "op": "<", "value": 0}, d("1"), None) is None
    assert rule_hit({"metric": "volume", "op": "<", "value": 1}, d("1"), None) is None


def test_spot_reexports_same_function():
    from trader.features.spot import monitor_rules

    assert monitor_rules.rule_hit is rule_hit
