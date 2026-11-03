"""kernel/session/schemas — 세션 공용 토론 스키마."""

import json

import pytest
from pydantic import ValidationError

from trader.kernel.session.schemas import AnalystReport, DebateTurn, json_schema


def _turn(**over) -> dict:
    d = {
        "side": "bull",
        "round": 0,
        "stance": "BULLISH",
        "valuation_view": "FAIR",
        "arguments": ["합성 논거"],
        "rebuttals": [],
        "timing": {"action": "WAIT"},
        "conviction": 0.6,
        "what_would_change_my_mind": ["합성 조건"],
    }
    d.update(over)
    return d


def test_debate_turn_round_and_side_validate():
    t = DebateTurn.model_validate(_turn(side="bear", round=2, stance="BEARISH"))
    assert (t.side, t.round) == ("bear", 2)
    with pytest.raises(ValidationError):
        DebateTurn.model_validate(_turn(round=-1))
    with pytest.raises(ValidationError):
        DebateTurn.model_validate(_turn(side="neutral"))
    with pytest.raises(ValidationError):
        DebateTurn.model_validate(_turn(extra_field=1))


def test_json_schema_mentions_required():
    schema = json.loads(json_schema(AnalystReport))
    assert "role" in schema["required"] and "evidence" in schema["required"]
    assert '"required"' in json_schema(DebateTurn)


def test_quant_report_validates_probabilities_and_role():
    from trader.kernel.session.schemas import QuantReport

    base = {
        "ticker": "005930",
        "asof": "2026-10-09",
        "model": "m1",
        "p_up": 0.5,
        "p_flat": 0.3,
        "p_down": 0.2,
        "signal": "BUY",
        "horizon_days": 5,
        "features_used": ["ret_5d"],
    }
    r = QuantReport.model_validate(base)
    assert r.role == "quant" and r.data_gaps == [] and r.label_mode == "absolute"
    with pytest.raises(ValidationError):
        QuantReport.model_validate({**base, "p_up": 1.5})
    with pytest.raises(ValidationError):
        QuantReport.model_validate({**base, "signal": "MAYBE"})
