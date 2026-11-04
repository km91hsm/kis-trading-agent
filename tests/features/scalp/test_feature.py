"""scalp 기능 선언."""

import pytest
from pydantic import ValidationError

from trader.features.scalp.fake import FAKE_ADVICE, FAKE_VERDICT
from trader.features.scalp.feature import FEATURE
from trader.features.scalp.schemas import ScalpAdvice, ScalpVerdict


def test_feature_is_lane_without_origins_or_migrations():
    assert FEATURE.id == "scalp" and FEATURE.tier == "lane"
    assert FEATURE.origins == () and FEATURE.migrations == ()
    assert [s.key for s in FEATURE.sections] == ["scalp"]


def test_fake_payloads_validate_and_schemas_forbid_extras():
    assert ScalpVerdict.model_validate(FAKE_VERDICT).go is False
    assert ScalpAdvice.model_validate(FAKE_ADVICE).action == "hold"
    with pytest.raises(ValidationError):
        ScalpVerdict.model_validate({**FAKE_VERDICT, "x": 1})
    with pytest.raises(ValidationError):
        ScalpVerdict.model_validate({**FAKE_VERDICT, "take_profit_pct": 0.06})
    with pytest.raises(ValidationError):
        ScalpAdvice.model_validate({**FAKE_ADVICE, "action": "buy"})
