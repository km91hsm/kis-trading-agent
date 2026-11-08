"""kernel/session/debate — 두 판 같은 편 합치기(merge_turns·merge_lanes)."""

import pytest

from tests.features.spot.helpers import ok_turn
from trader.kernel.session.debate import merge_lanes, merge_turns, research_summary
from trader.kernel.session.schemas import DebateTurn


def turn(side, stance, c, round=0, **kw):
    return DebateTurn.model_validate(ok_turn(side, round, stance=stance, conviction=c, **kw))


def _calm() -> dict:
    r0 = {"bull": turn("bull", "BULLISH", 0.7), "bear": turn("bear", "BEARISH", 0.7)}
    final = {
        "bull": turn("bull", "BULLISH", 0.7, round=1),
        "bear": turn("bear", "BEARISH", 0.7, round=1),
    }
    return research_summary(r0, final, 1)


def _sycophantic() -> dict:
    r0 = {"bull": turn("bull", "BULLISH", 0.8), "bear": turn("bear", "BEARISH", 0.7)}
    final = {
        "bull": turn("bull", "BULLISH", 0.6, round=1),
        "bear": turn("bear", "BULLISH", 0.5, round=1),  # 근거 없이 동조
    }
    return research_summary(r0, final, 1)


def test_merge_turns_signed_mean_and_threshold():
    m = merge_turns([turn("bull", "BULLISH", 0.8), turn("bull", "BEARISH", 0.4)])
    assert m.stance == "BULLISH" and m.conviction == pytest.approx(0.2)
    m = merge_turns([turn("bull", "BULLISH", 0.3), turn("bull", "BEARISH", 0.2)])
    assert m.stance == "NEUTRAL" and m.conviction == pytest.approx(0.05)
    assert m.issues == [] and m.responses == [] and m.replies == []
    m = merge_turns([turn("bear", "BEARISH", 0.5), turn("bear", "BEARISH", 0.3)])
    assert m.stance == "BEARISH" and m.conviction == pytest.approx(0.4)


def test_merge_turns_primary_and_interleave():
    a = turn("bull", "BULLISH", 0.5, arguments=["a1", "a2", "a3"], valuation_view="FAIR")
    b = turn(
        "bull", "BULLISH", 0.9, arguments=["b1", "a1", "b2", "b3"], valuation_view="UNDERVALUED"
    )
    m = merge_turns([a, b])
    assert m.arguments == ["a1", "b1", "a2", "a3", "b2"] and m.valuation_view == "UNDERVALUED"


def test_merge_turns_neutral_takes_lane_a_as_primary():
    a = turn("bull", "BULLISH", 0.3, valuation_view="FAIR")
    b = turn("bull", "BEARISH", 0.3, valuation_view="OVERVALUED")
    m = merge_turns([a, b])
    assert m.stance == "NEUTRAL" and m.valuation_view == "FAIR"


def test_merge_turns_rejects_mixed_side_or_round():
    with pytest.raises(ValueError):
        merge_turns([turn("bull", "BULLISH", 0.5), turn("bear", "BEARISH", 0.5)])
    with pytest.raises(ValueError):
        merge_turns([turn("bull", "BULLISH", 0.5), turn("bull", "BULLISH", 0.5, round=1)])


def test_single_lane_summary_is_unchanged():
    s = _calm()
    assert merge_lanes({"a": s}) is s


def test_warning_flags_are_or_of_lanes():
    calm, syc = _calm(), _sycophantic()
    out = merge_lanes({"a": calm, "b": syc})
    assert out["sycophantic"] is True and out["preferred"] == "round0"
    assert out["by_lane"]["b"]["sycophantic"] is True and out["lanes"] == ["a", "b"]
    assert out["by_lane"]["a"] is calm
    DebateTurn.model_validate(out["final"]["bull"])  # 합친 턴은 DebateTurn 모양
    # 차이·시점 일치는 합친 턴으로 다시 계산한다(bear 최종 평균 -0.1 → NEUTRAL)
    assert out["final"]["bear"]["stance"] == "NEUTRAL"
    assert out["gap_round0"] == pytest.approx(1.45) and out["gap_final"] == pytest.approx(0.65)
    assert out["convergence"] is None and out["convergence_review"] is False
    assert out["rounds"] == 1 and out["timing_agree"] is True


def test_calm_lanes_keep_final_preferred():
    out = merge_lanes({"a": _calm(), "b": _calm()})
    assert out["sycophantic"] is False and out["preferred"] == "final"


def test_merge_lanes_rejects_different_rounds():
    one = _calm()
    zero = research_summary(
        {"bull": turn("bull", "BULLISH", 0.7), "bear": turn("bear", "BEARISH", 0.7)},
        {"bull": turn("bull", "BULLISH", 0.7), "bear": turn("bear", "BEARISH", 0.7)},
        0,
    )
    with pytest.raises(ValueError, match="두 판의 마지막 라운드가 다릅니다"):
        merge_lanes({"a": one, "b": zero})
