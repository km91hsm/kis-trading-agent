"""왕복 비용 상수(커널 승격, M8 Ruling A5)."""

from trader.features.scan import backtest
from trader.features.spot import costs as spot_costs
from trader.kernel.execution import costs as kernel_costs


def test_round_trip_bp_table():
    assert kernel_costs.round_trip_bp("KR") == 30
    assert kernel_costs.round_trip_bp("US") == 50
    assert kernel_costs.round_trip_bp("XX") == 50  # 모르는 시장은 US
    assert spot_costs.round_trip_bp is kernel_costs.round_trip_bp
    assert backtest.ROUND_TRIP_BP is kernel_costs.ROUND_TRIP_BP
