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
    # convergence 는 판별 값 중 가장 경고적인 값(판 b 의 unjustified) — sycophantic 과 일치
    assert out["convergence"] == "unjustified" and out["convergence_review"] is False
    assert out["convergence_by_lane"] == {"a": None, "b": "unjustified"}
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


def _lane(bull0, bear0, bull1, bear1) -> dict:
    r0 = {"bull": turn("bull", "BULLISH", bull0), "bear": turn("bear", "BEARISH", bear0)}
    final = {
        "bull": turn("bull", "BULLISH", bull1, round=1),
        "bear": turn("bear", "BEARISH", bear1, round=1),
    }
    return research_summary(r0, final, 1, evidence_ids=set())


def test_merged_convergence_is_worst_lane_value_not_recomputed():
    a = _lane(0.25, 0.3, 0.15, 0.15)  # 입장 변화 없음
    b = _lane(0.15, 0.3, 0.05, 0.05)  # 확신 붕괴 → uncertainty
    out = merge_lanes({"a": a, "b": b}, evidence_ids=set())
    assert out["convergence_by_lane"] == {"a": a["convergence"], "b": b["convergence"]}
    assert out["convergence"] == "uncertainty"
    assert out["sycophantic"] is False and out["preferred"] == "final"
    assert out["convergence"] != "unjustified"  # preferred=="final" 과 모순되지 않는다


def test_merged_convergence_priority_order():
    from trader.kernel.session import debate

    worst = debate._worst_convergence
    assert worst([None, "justified"]) == "justified"
    assert worst(["justified", "uncertainty"]) == "uncertainty"
    assert worst(["uncertainty", "unverified"]) == "unverified"
    assert worst(["unverified", "unjustified"]) == "unjustified"
    assert worst([None, None]) is None


def _sc(from_stance, reason="이유", evidence=("s1",)):
    return {"from_stance": from_stance, "reason": reason, "evidence": list(evidence)}


def _two_lane(bull0, bull1) -> dict:
    """bull 측만 판별로 다르게: bull0·bull1 = {lane: DebateTurn}. bear 는 고정."""
    bear0, bear1 = turn("bear", "BEARISH", 0.7), turn("bear", "BEARISH", 0.7, round=1)
    return {
        lane: research_summary(
            {"bull": bull0[lane], "bear": bear0}, {"bull": bull1[lane], "bear": bear1}, 1
        )
        for lane in ("a", "b")
    }


def test_merged_stance_change_rebased_on_merged_round0():
    """주 턴(판 b)의 stance_change 는 BEARISH→ 이지만 합친 round0 은 NEUTRAL — 합친 턴의
    from_stance 는 합친 round0 대표 stance 로 다시 만든다(이유·근거는 주 턴 것)."""
    r0 = {"a": turn("bull", "NEUTRAL", 0.5), "b": turn("bull", "BEARISH", 0.1)}
    final = {
        "a": turn("bull", "BULLISH", 0.5, round=1, stance_change=_sc("NEUTRAL", "a 이유")),
        "b": turn("bull", "BULLISH", 0.9, round=1, stance_change=_sc("BEARISH", "b 이유")),
    }
    out = merge_lanes(_two_lane(r0, final))
    assert out["round0"]["bull"]["stance"] == "NEUTRAL"
    assert out["final"]["bull"]["stance"] == "BULLISH"
    assert out["final"]["bull"]["stance_change"] == _sc("NEUTRAL", "b 이유")
    DebateTurn.model_validate(out["final"]["bull"])


def test_merged_stance_change_none_when_merged_stance_unchanged():
    """합친 round0·final 대표 stance 가 같으면 주 턴에 stance_change 가 있어도 None."""
    r0 = {"a": turn("bull", "BEARISH", 0.1), "b": turn("bull", "BULLISH", 0.9)}
    final = {
        "a": turn("bull", "BULLISH", 0.9, round=1, stance_change=_sc("BEARISH")),
        "b": turn("bull", "BULLISH", 0.5, round=1),
    }
    out = merge_lanes(_two_lane(r0, final))
    assert out["round0"]["bull"]["stance"] == out["final"]["bull"]["stance"] == "BULLISH"
    assert out["final"]["bull"]["stance_change"] is None


def test_merged_stance_change_none_when_primary_stance_differs_from_merged_final():
    """중립 평균이라 주 턴(판 a, BULLISH)의 stance 가 합친 final(NEUTRAL)과 다르면 주 턴의 전환
    근거(→BULLISH)는 합친 턴의 전환을 설명하지 못한다 — None."""
    r0 = {"a": turn("bull", "BEARISH", 0.5), "b": turn("bull", "BEARISH", 0.5)}
    final = {
        "a": turn("bull", "BULLISH", 0.3, round=1, stance_change=_sc("BEARISH", "a 이유")),
        "b": turn("bull", "BEARISH", 0.3, round=1),
    }
    out = merge_lanes(_two_lane(r0, final))
    assert out["round0"]["bull"]["stance"] == "BEARISH"
    assert out["final"]["bull"]["stance"] == "NEUTRAL"
    assert out["final"]["bull"]["stance_change"] is None
    DebateTurn.model_validate(out["final"]["bull"])
