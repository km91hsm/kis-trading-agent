"""파생 결정 스키마(옛 tests/agents/test_schemas_fo.py 중 스키마 검증 이식)."""

import pytest
from pydantic import ValidationError

from trader.features.fo import schemas


def _open(**kw):
    base = dict(intent="OPEN", direction="LONG", vehicle="FUTURES", target_leverage=1.0,
                stop_pt=340.0)  # fmt: skip
    return schemas.FoDecision(**(base | kw))


def test_fo_decision_validators():
    assert _open().intent == "OPEN"
    with pytest.raises(ValidationError, match="stop_pt"):
        schemas.FoDecision(intent="OPEN", direction="LONG", vehicle="FUTURES", target_leverage=1)
    with pytest.raises(ValidationError, match="FLAT 은 HOLD 또는 CLOSE 만 가능합니다"):
        _open(direction="FLAT")
    with pytest.raises(ValidationError, match="양의 target_leverage"):
        _open(target_leverage=0)
    schemas.FoDecision(intent="HOLD", direction="FLAT", vehicle="FUTURES")  # HOLD·CLOSE 는 허용
    schemas.FoDecision(intent="CLOSE", direction="FLAT", vehicle="FUTURES")


def test_envelope_forbids_extra_and_defaults():
    ok = _open()
    with pytest.raises(ValidationError):
        schemas.FoDecisionEnvelope(decision=ok.model_dump(), request_id="r1", extra_field="x")
    env = schemas.FoDecisionEnvelope(decision=ok.model_dump(), request_id="r1")
    assert env.account_id is None and env.position_id is None
    with pytest.raises(ValidationError):
        schemas.FoDecisionEnvelope(decision=ok.model_dump(), request_id="")


def test_invalidation_rule_finite_positive():
    schemas.FoInvalidationRule(op="<", value=340.0)
    with pytest.raises(ValidationError):
        schemas.FoInvalidationRule(op="<", value=float("inf"))
    with pytest.raises(ValidationError):
        schemas.FoInvalidationRule(op="<", value=0)


def test_side_and_kind_helpers():
    assert schemas.contract_side_of("LONG", "FUTURES") == "LONG"
    assert schemas.contract_side_of("SHORT", "FUTURES") == "SHORT"
    assert schemas.contract_side_of("SHORT", "OPTION_BUY") == "LONG"
    assert schemas.contract_side_of("LONG", "OPTION_SELL") == "SHORT"
    assert schemas.kind_of("LONG", "FUTURES") == "FUTURES"
    assert schemas.kind_of("LONG", "OPTION_BUY") == "CALL"
    assert schemas.kind_of("LONG", "OPTION_SELL") == "PUT"
    assert schemas.kind_of("SHORT", "OPTION_BUY") == "PUT"
    assert schemas.kind_of("SHORT", "OPTION_SELL") == "CALL"
    assert schemas.order_side("LONG", entry=True) == "BUY"
    assert schemas.order_side("LONG", entry=False) == "SELL"
    assert schemas.order_side("SHORT", entry=True) == "SELL"
    assert schemas.kind_sign("PUT") == -1 and schemas.kind_sign("CALL") == 1
