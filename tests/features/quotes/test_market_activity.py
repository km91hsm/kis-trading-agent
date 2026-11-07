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


def test_latest_partition_queries_have_bounded_work_when_success_never_exists(conn):
    """이력이 늘어나도 성공 없는 partition을 찾느라 archive 전체를 훑지 않아야 한다."""
    from trader.features.quotes import activity_store

    for i in range(512):
        activity_store.append(
            conn,
            "US",
            received_at=T + timedelta(seconds=30 * i),
            partitions={
                "NAS:turnover": {"ok": True, "rows": [{"ticker": "AAA"}]},
                "AMS:loss": {"ok": False, "rows": [], "error": "TimeoutError"},
            },
        )
    statements = []
    conn.set_trace_callback(statements.append)
    instructions = []
    conn.set_progress_handler(lambda instructions=instructions: instructions.append(1) or 0, 1)
    try:
        assert (
            activity_store.latest(conn, "US", "AMS:loss", asof=T + timedelta(days=1), success=True)
            is None
        )
    finally:
        conn.set_progress_handler(None, 0)
        conn.set_trace_callback(None)
    assert len(instructions) < 500, f"absent-success lookup executed {len(instructions)} VM steps"
    plan = conn.execute("EXPLAIN QUERY PLAN " + statements[-1]).fetchall()
    assert not any("SCAN" in row[3] and "CONSTANT" not in row[3] for row in plan)
    assert not any("TEMP B-TREE" in row[3] for row in plan)


def test_latest_partition_index_keeps_pit_and_last_success_semantics(conn):
    from trader.features.quotes import activity_store

    put(conn, T, "100")
    for i in range(1, 513):
        put(conn, T + timedelta(seconds=30 * i), error="TimeoutError")
    for success in (False, True):
        instructions = []
        conn.set_progress_handler(lambda instructions=instructions: instructions.append(1) or 0, 1)
        try:
            got = activity_store.latest(
                conn, "US", "NAS:turnover", asof=T + timedelta(seconds=60), success=success
            )
        finally:
            conn.set_progress_handler(None, 0)
        assert got[0] == (T if success else T + timedelta(seconds=60)).isoformat()
        assert len(instructions) < 500
    # 장기간 실패해도 마지막 성공을 버리지 않으며, 그 조회도 과거 전체 역스캔이 아니다.
    instructions = []
    conn.set_progress_handler(lambda instructions=instructions: instructions.append(1) or 0, 1)
    try:
        got = activity_store.latest(
            conn, "US", "NAS:turnover", asof=T + timedelta(days=1), success=True
        )
    finally:
        conn.set_progress_handler(None, 0)
    assert got[0] == T.isoformat() and got[1]["rows"][0]["turnover"] == "100"
    assert len(instructions) < 500


def test_append_rolls_back_archive_when_partition_metadata_write_fails(conn):
    from trader.features.quotes import activity_store

    conn.execute("""CREATE TRIGGER reject_partition BEFORE INSERT ON market_activity_partitions
        BEGIN SELECT RAISE(ABORT, 'synthetic metadata storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic metadata storage failure"):
        activity_store.append(
            conn,
            "US",
            received_at=T,
            partitions={"NAS:turnover": {"ok": True, "rows": []}},
        )
    assert conn.execute("SELECT count(*) FROM market_activity_batches").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM market_activity_partitions").fetchone()[0] == 0


def test_legacy_turnover_has_explicit_unknown_unit_through_research_compaction(conn):
    from trader.kernel.session.tooling import compact_context

    put(conn, T, "123456.78")
    snapshot = api.activity_snapshot(conn, "US", asof=T)
    context = compact_context({"market_activity": snapshot})
    row = context["market_activity"]["rankings"]["turnover"][0]
    assert row["turnover"] == "123456.78" and row["currency"] == "USD"
    assert row["turnover_unit"] == "unknown"
    assert row["turnover_basis"] == "source_reported"
    assert row["turnover_source_field"] is None
    assert "turnover_unit_unverified" in context["market_activity"]["data_gaps"]
