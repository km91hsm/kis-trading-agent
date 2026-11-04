"""fo 설정(옛 tests/test_config_fo.py 이식): FoCfg·하위 모델·schedule fo."""

from decimal import Decimal

import pydantic
import pytest

from trader.features.fo.settings import SECTIONS, FoCfg, FoMarketSchedule, FoSchedule
from trader.kernel.broker.models import FoProduct


def test_fo_defaults_disabled_and_mini():
    fo = FoCfg()
    assert fo.enabled is False and fo.product == "mini"
    p = fo.product_cfg()
    assert isinstance(p, FoProduct)
    assert p.multiplier == 50000 and p.futures_prefix == ("A05", "105")
    assert len(p.option_ticks) == 3 and p.option_ticks[-1][0] is None
    assert p.futures_tick == Decimal("0.02")
    assert fo.vehicles == ["FUTURES", "OPTION_BUY", "OPTION_SELL"]
    assert fo.max_leverage == 2.0 and fo.max_positions == 1 and fo.max_contracts == 4
    assert fo.option.sell_stop_mult.default == 2.0
    assert fo.margin_rate.futures == 0.12 and fo.margin_rate.max_age_days == 30
    assert fo.stress.index_pct == [0.03, 0.06]
    assert fo.products["regular"].multiplier == 250000


def test_fo_forbid_unknown_key():
    with pytest.raises(pydantic.ValidationError, match="foo"):
        FoCfg(foo=1)


def test_product_known_message():
    with pytest.raises(pydantic.ValidationError, match="fo.product 'x' 가 products 에 없습니다"):
        FoCfg(product="x")


def test_sell_stop_mult_order():
    with pytest.raises(pydantic.ValidationError, match="sell_stop_mult"):
        FoCfg(option={"sell_stop_mult": {"min": 2.5, "default": 2.0, "max": 3.0}})
    with pytest.raises(pydantic.ValidationError, match="sell_stop_mult"):
        FoCfg(option={"sell_stop_mult": {"min": 1.5, "default": 3.5, "max": 3.0}})


def test_regime_cap_zero_vs_null():
    fo = FoCfg(max_leverage=2.0, leverage_by_regime={"bear": 0, "neutral": None})
    assert fo.regime_cap("bear") == 0.0
    assert fo.regime_cap("neutral") == 2.0
    assert fo.regime_cap("bull") == 2.0
    fo2 = FoCfg(max_leverage=2.0, leverage_by_regime={"bull": 3.0})
    assert fo2.regime_cap("bull") == 2.0  # 설정 한도보다 넓어지지 않는다


def test_max_leverage_range():
    with pytest.raises(pydantic.ValidationError):
        FoCfg(max_leverage=0)
    with pytest.raises(pydantic.ValidationError):
        FoCfg(max_leverage=5.5)


def test_entry_window_hm_validated():
    with pytest.raises(pydantic.ValidationError, match="HH:MM"):
        FoCfg(entry_window={"start": "9:00"})


def test_settings_has_fo_block(settings):
    assert settings.fo.enabled is True  # conftest 가 켠 값
    assert settings.fo.product == "mini"


def test_schedule_fo_defaults():
    sch = FoSchedule()
    assert sch.kr.session == "08:20"
    assert sch.kr.monitor.start == "08:45" and sch.kr.monitor.end == "15:45"
    assert sch.kr.monitor.interval_s == 60
    with pytest.raises(pydantic.ValidationError):
        FoMarketSchedule(session="8:20")
    with pytest.raises(pydantic.ValidationError):
        FoMarketSchedule(monitor={"start": "08:45", "end": "15:45", "interval_s": 1})


def test_sections_declared():
    assert [(s.file, s.key) for s in SECTIONS] == [("settings", "fo"), ("schedule", "fo")]


def test_option_ticks_validation():
    """Codex 단계 1 P2: tick 은 양수, 상한은 엄격한 오름차순, inf 는 마지막에만."""
    base = dict(
        futures_prefix="105", call_prefix="205", put_prefix="305", multiplier=50000,
        futures_tick="0.02", name="x",
    )  # fmt: skip
    bad = [
        [["3", "0"], ["inf", "0.05"]],
        [["3", "-0.01"], ["inf", "0.05"]],
        [["10", "0.01"], ["3", "0.02"], ["inf", "0.05"]],
        [["3", "0.01"], ["3", "0.02"], ["inf", "0.05"]],
        [["inf", "0.01"], ["3", "0.02"], ["inf", "0.05"]],
        [["3", "0.01"]],
    ]
    for ticks in bad:
        with pytest.raises(ValueError):
            FoProduct(**base, option_ticks=ticks)
    ok = FoProduct(**base, option_ticks=[["3", "0.01"], ["10", "0.02"], ["inf", "0.05"]])
    assert ok.option_ticks[0] == (Decimal("3"), Decimal("0.01"))
