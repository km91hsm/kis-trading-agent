"""Model-facing quality distinguishes observation dates from fetch timestamps."""

from datetime import UTC, datetime

from trader.features.ingest import input_quality


def test_monthly_macro_keeps_observation_date_and_fetch_freshness_separate():
    data = {
        "cpi": {
            "data": {"value": 3.1, "date": "2026-10", "frequency": "M"},
            "received_at": "2026-11-07T00:00:00+00:00",
            "error": None,
        }
    }
    out = input_quality.macro_context(data, asof=datetime(2026, 11, 7, tzinfo=UTC))
    assert out["cpi"]["source_asof"] == "2026-10"
    assert out["cpi"]["received_at"] == "2026-11-07T00:00:00+00:00"
    assert out["cpi"]["stale"] is None
    assert out["cpi"]["status"] == "available"
    assert out["cpi"]["data"]["value"] == 3.1
    assert out["cpi"]["data_gaps"]


def test_macro_future_observation_is_not_exposed():
    data = {
        "vix": {
            "data": {"value": 80, "date": "2026-11-08"},
            "received_at": "2026-11-07T00:00:00+00:00",
            "error": None,
        }
    }
    out = input_quality.macro_context(data, asof=datetime(2026, 11, 7, tzinfo=UTC))
    assert out["vix"]["data"] is None
    assert out["vix"]["status"] == "unavailable"


def test_activity_rejects_other_market_and_future_poll():
    from types import SimpleNamespace

    asof = datetime(2026, 11, 7, tzinfo=UTC)
    for snapshot in (
        {"market": "US", "received_at": "2026-11-07T00:00:00+00:00"},
        {"market": "KR", "received_at": "2026-11-07T00:00:01+00:00"},
    ):
        out = input_quality.activity_context(
            object(),
            "KR",
            asof=asof,
            port=SimpleNamespace(snapshot=lambda *a, snapshot=snapshot, **k: snapshot),
        )
        assert out["status"] == "unavailable"
        assert out["rankings"]["turnover"] == []
        assert out["data_gaps"]


def test_requested_macro_coverage_distinguishes_dxy_proxy_and_nasdaq_composite():
    macro = input_quality.macro_context(
        {
            "dxy": {
                "data": {"value": 120, "date": "2026-11-06"},
                "received_at": "2026-11-07",
                "error": None,
            },
            "nasdaq": {
                "data": {"value": 20000, "date": "2026-11-06"},
                "received_at": "2026-11-07",
                "error": None,
            },
        },
        asof=datetime(2026, 11, 7, tzinfo=UTC),
    )
    coverage = input_quality.source_coverage(
        macro,
        blog_config=["ranto28"],
        blog_posts=[{"source": "naver_blog/ranto28", "title": "시장 의견"}],
    )
    assert set(coverage["macro"]) == {"sp500", "nasdaq", "wti", "dxy", "us10y"}
    assert coverage["macro"]["sp500"]["status"] == "missing"
    assert coverage["macro"]["nasdaq"]["series_id"] == "NASDAQCOM"
    assert coverage["macro"]["nasdaq"]["instrument"] == "NASDAQ Composite"
    assert coverage["macro"]["dxy"]["exact_requested_instrument"] is False
    assert coverage["macro"]["dxy"]["instrument"] == "Federal Reserve broad US dollar index"
    assert coverage["macro"]["dxy"]["source"] == "FRED"
    assert coverage["naver_blog/ranto28"]["status"] == "present"
    assert coverage["naver_blog/ranto28"]["configured"] is True
