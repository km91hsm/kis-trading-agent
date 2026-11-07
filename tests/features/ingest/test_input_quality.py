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


def test_intraday_macro_rejects_same_day_future_timestamp_after_timezone_conversion():
    asof = datetime(2026, 11, 7, 1, tzinfo=UTC)
    for observed in ("2026-11-07T05:00:00+00:00", "2026-11-07T14:00:00+09:00"):
        out = input_quality.macro_context(
            {
                "kospi": {
                    "data": {"value": 3000, "date": "2026-11-07", "asof": observed},
                    "received_at": "2026-11-07T01:00:00+00:00",
                    "error": None,
                }
            },
            asof=asof,
        )
        assert out["kospi"]["data"] is None
        assert out["kospi"]["status"] == "unavailable"
        assert out["kospi"]["source_asof"] == observed
    current = input_quality.macro_context(
        {
            "kospi": {
                "data": {"value": 2990, "asof": "2026-11-07T09:59:59+09:00"},
                "received_at": "2026-11-07T01:00:00+00:00",
                "error": None,
            }
        },
        asof=asof,
    )
    assert current["kospi"]["data"]["value"] == 2990


def test_selected_index_metadata_survives_repeated_macro_validation_and_coverage():
    raw = {
        "sp500": {
            "data": {"value": 4200, "date": "2026-11-05"},
            "error": None,
            "source": "KIS",
            "series_id": "SPX",
            "instrument": "S&P 500",
            "basis": "daily_observation",
            "unit": "index points",
            "observed_date": "2026-11-05",
            "observed_at": None,
            "timestamp_precision": "day",
            "session": "daily_observation",
            "live_verified": False,
            "fallback_reason": None,
            "received_at": "2026-11-07T00:00:00+00:00",
            "data_gaps": ["source timing unavailable"],
        }
    }
    asof = datetime(2026, 11, 7, tzinfo=UTC)
    checked = input_quality.macro_context(raw, asof=asof)
    checked = input_quality.macro_context(checked, asof=asof)
    coverage = input_quality.source_coverage(checked)["macro"]["sp500"]
    assert checked["sp500"]["source"] == "KIS"
    assert "source timing unavailable" in checked["sp500"]["data_gaps"]
    assert len(checked["sp500"]["data_gaps"]) == len(set(checked["sp500"]["data_gaps"]))
    for key in (
        "source",
        "series_id",
        "instrument",
        "basis",
        "unit",
        "observed_date",
        "observed_at",
        "timestamp_precision",
        "session",
        "live_verified",
        "fallback_reason",
    ):
        assert coverage[key] == raw["sp500"][key]
    assert coverage["requested_source"] == "S&P 500"
    assert coverage["source_asof"] == "2026-11-05"
    assert coverage["exact_requested_instrument"] is True


def test_selected_fred_fallback_reason_reaches_proxy_coverage():
    macro = input_quality.macro_context(
        {
            "nasdaq": {
                "data": {"value": 20000, "date": "2026-11-05"},
                "error": None,
                "source": "FRED",
                "series_id": "NASDAQCOM",
                "instrument": "NASDAQ Composite",
                "basis": "daily_close",
                "fallback_reason": "KIS method unavailable",
                "received_at": "2026-11-07T00:00:00+00:00",
            }
        },
        asof=datetime(2026, 11, 7, tzinfo=UTC),
    )
    coverage = input_quality.source_coverage(macro)["macro"]
    assert coverage["nasdaq"]["source"] == "FRED"
    assert coverage["nasdaq"]["fallback_reason"] == "KIS method unavailable"
    assert "KIS method unavailable" in coverage["nasdaq"]["data_gaps"]
    assert coverage["dxy"]["exact_requested_instrument"] is False
    assert coverage["wti"]["exact_requested_instrument"] is False
    assert coverage["us10y"]["exact_requested_instrument"] is False
