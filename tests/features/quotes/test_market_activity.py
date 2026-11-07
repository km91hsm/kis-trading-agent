import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from trader.features.quotes import api
from trader.features.quotes.migrations import MIGRATIONS

T = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    for statement in MIGRATIONS[0].statements:
        c.execute(statement)
    yield c
    c.close()


def put(
    conn,
    when,
    amount="100",
    *,
    ticker="AAA",
    exchange="NAS",
    error=None,
    session="regular_poll_window",
    day="2026-11-06",
):
    from trader.features.quotes import activity_store

    rows = (
        []
        if ticker is None
        else [
            {
                "ticker": ticker,
                "name": "Synthetic",
                "market": "US",
                "exchange": exchange,
                "currency": "USD",
                "source_rank": 2,
                "price": "10",
                "change_pct": 0.1,
                "volume": 10,
                "turnover": amount,
                "source_asof": None,
                "basis": "unknown",
                "observed_day": day,
                "observation_session": session,
            }
        ]
    )
    p = {"rows": rows, "ok": error is None, "pages": 1, "truncated": False, "error": error}
    activity_store.append(conn, "US", received_at=when, partitions={exchange + ":turnover": p})


def test_no_data_is_explicit_and_market_scoped(conn):
    assert hasattr(api, "activity_snapshot"), "stored activity API required"
    got = api.activity_snapshot(conn, "US", asof=T)
    assert got["status"] == "unavailable" and got["stale"]
    assert got["rankings"] == {"turnover": [], "gain": [], "loss": []}
    assert got["currency"] == "USD" and got["source_asof"] is None
    with pytest.raises(ValueError):
        api.activity_snapshot(conn, "ALL", asof=T)


def test_asof_excludes_future_and_preserves_last_success_with_latest_failure(conn):
    assert hasattr(api, "activity_snapshot"), "stored activity API required"
    put(conn, T, "100")
    put(conn, T + timedelta(seconds=30), error="TimeoutError")
    put(conn, T + timedelta(seconds=90), "999")
    got = api.activity_snapshot(conn, "US", asof=T + timedelta(seconds=60))
    row = got["rankings"]["turnover"][0]
    assert row["turnover"] == "100"
    assert got["status"] == "partial" and got["errors"]
    assert row["stale"]  # latest failure makes prior success stale even within TTL
    assert row["received_at"] == T.isoformat()
    assert api.activity_snapshot(conn, "KR", asof=T)["status"] == "unavailable"
    assert api.activity_snapshot(conn, "US", asof=T + timedelta(seconds=300))["stale"]


def test_rank_drop_is_unknown_not_zero_or_not_ranked(conn):
    assert hasattr(api, "activity_snapshot"), "stored activity API required"
    put(conn, T, "100")
    put(conn, T + timedelta(seconds=30), ticker=None)
    got = api.activity_for_ticker(
        api.activity_snapshot(conn, "US", asof=T + timedelta(seconds=30)), "AAA"
    )
    assert got["status"] == "unavailable"
    assert got["rankings"] == {"turnover": [], "gain": [], "loss": []}
    assert got["data_gaps"]


def test_growth_compares_actual_observed_five_minute_increments(conn):
    assert hasattr(api, "activity_snapshot"), "stored activity API required"
    put(conn, T, "100")
    put(conn, T + timedelta(minutes=5), "200")
    put(conn, T + timedelta(minutes=10), "450")
    got = api.activity_snapshot(conn, "US", asof=T + timedelta(minutes=10))
    growth = got["rankings"]["turnover"][0]["growth"]
    assert growth["status"] == "available" and growth["value"] == 1.5
    assert (growth["recent_5m"], growth["previous_5m"]) == ("250", "100")
    assert growth["basis"] == "observed_api_cumulative_turnover"
    assert got["source_asof"] is None and got["basis"] == "unknown"


@pytest.mark.parametrize(
    "middle,last,session,day",
    [
        ("100", "200", "regular_poll_window", "2026-11-06"),
        ("200", "150", "regular_poll_window", "2026-11-06"),
        (None, "450", "regular_poll_window", "2026-11-06"),
        ("200", "450", "different", "2026-11-06"),
        ("200", "450", "regular_poll_window", "2026-11-07"),
    ],
)
def test_growth_rejects_zero_denominator_reset_missing_or_different_session(
    conn, middle, last, session, day
):
    assert hasattr(api, "activity_snapshot"), "stored activity API required"
    put(conn, T, "100")
    put(conn, T + timedelta(minutes=5), middle)
    put(conn, T + timedelta(minutes=10), last, session=session, day=day)
    g = api.activity_snapshot(conn, "US", asof=T + timedelta(minutes=10))["rankings"]["turnover"][
        0
    ]["growth"]
    assert g["status"] == "unavailable" and g["value"] is None


def test_growth_requires_three_boundaries_within_sixty_seconds(conn):
    assert hasattr(api, "activity_snapshot"), "stored activity API required"
    put(conn, T, "100")
    put(conn, T + timedelta(minutes=3), "200")
    put(conn, T + timedelta(minutes=10), "450")
    g = api.activity_snapshot(conn, "US", asof=T + timedelta(minutes=10))["rankings"]["turnover"][
        0
    ]["growth"]
    assert g["status"] == "unavailable"


def test_merges_venues_by_decimal_without_overwriting_source_rank(conn):
    assert hasattr(api, "activity_snapshot"), "stored activity API required"
    put(conn, T, "100", exchange="NAS")
    put(conn, T, "900", ticker="BBB", exchange="NYS")
    got = api.activity_snapshot(conn, "US", asof=T)
    assert [r["ticker"] for r in got["rankings"]["turnover"]] == ["BBB", "AAA"]
    assert [r["source_rank"] for r in got["rankings"]["turnover"]] == [2, 2]
    assert not got["coverage"]["complete"]


def test_individual_partition_receipt_does_not_become_fresh_at_batch_finish(conn):
    assert hasattr(api, "activity_snapshot")
    from trader.features.quotes import activity_store

    activity_store.append(
        conn,
        "US",
        received_at=T + timedelta(seconds=150),
        partitions={
            "NAS:turnover": {
                "ok": True,
                "rows": [
                    {
                        "ticker": "AAA",
                        "market": "US",
                        "exchange": "NAS",
                        "currency": "USD",
                        "turnover": "100",
                    }
                ],
                "received_at": T.isoformat(),
            }
        },
    )
    got = api.activity_snapshot(conn, "US", asof=T + timedelta(seconds=150))
    row = got["rankings"]["turnover"][0]
    assert row["received_at"] == T.isoformat() and row["stale"]


def test_growth_detects_counter_reset_between_boundary_samples(conn):
    put(conn, T, "100")
    put(conn, T + timedelta(minutes=5), "200")
    put(conn, T + timedelta(minutes=7), "10")
    put(conn, T + timedelta(minutes=10), "450")
    g = api.activity_snapshot(conn, "US", asof=T + timedelta(minutes=10))["rankings"]["turnover"][
        0
    ]["growth"]
    assert g["status"] == "unavailable" and g["reason"] == "counter_reset_or_negative_delta"


def test_growth_does_not_emit_infinite_json_numbers(conn):
    put(conn, T, "0")
    put(conn, T + timedelta(minutes=5), "1e-400")
    put(conn, T + timedelta(minutes=10), "1e400")
    g = api.activity_snapshot(conn, "US", asof=T + timedelta(minutes=10))["rankings"]["turnover"][
        0
    ]["growth"]
    assert g["status"] == "unavailable" and g["value"] is None


def test_coverage_exposes_venue_page_and_candidate_bounds(conn):
    from trader.features.quotes import activity_store

    activity_store.append(
        conn,
        "US",
        received_at=T,
        partitions={
            "NAS:turnover": {
                "ok": True,
                "pages": 2,
                "truncated": True,
                "max_pages": 2,
                "limit": 100,
                "rows": [],
            }
        },
    )
    got = api.activity_snapshot(conn, "US", asof=T)
    detail = got["coverage"].get("partitions", {}).get("NAS:turnover")
    assert detail == {
        "pages": 2,
        "truncated": True,
        "max_pages": 2,
        "limit": 100,
        "received_at": T.isoformat(),
        "status": "ok",
        "rows": 0,
    }
