"""scan 스키마: Screened·Discovered 한도."""

import pytest
from pydantic import ValidationError

from trader.features.scan import schemas


def _c(name, ticker=None, tier="universe", market="KR", conf=0.7, catalyst="촉매"):
    return {
        "name": name,
        "ticker": ticker,
        "market": market,
        "tier": tier,
        "catalyst": catalyst,
        "source_ids": ["news:1"],
        "confidence": conf,
    }


def test_screened_schema_limits():
    ok = schemas.Screened(picks=[{"ticker": "005930", "reason": "x"}], rejected=[])
    assert ok.picks[0].ticker == "005930"
    with pytest.raises(ValidationError):
        schemas.Screened(picks=[{"ticker": f"{i}", "reason": "x"} for i in range(6)], rejected=[])
    with pytest.raises(ValidationError):
        schemas.Screened(picks=[], rejected=[])


@pytest.mark.parametrize("tier", ["universe", "listed"])
def test_discovered_schema_limits(tier):
    with pytest.raises(ValidationError):
        schemas.Discovered(candidates=[_c(f"n{i}", tier=tier) for i in range(9)])
    ok = schemas.Discovered(candidates=[_c("n", tier=tier)])
    assert ok.candidates[0].tier == tier
