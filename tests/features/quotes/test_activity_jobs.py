from datetime import UTC, datetime
from types import SimpleNamespace

from tests.features.quotes.test_market_activity import put
from trader.features.quotes import api, jobs, routes
from trader.features.quotes.feature import FEATURE
from trader.features.quotes.settings import QuotesSettings
from trader.kernel.broker.market_activity import ActivityPage, ActivityRow
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

OPEN = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)


class Market:
    def __init__(self):
        self.calls = []

    def activity_rank(self, market, kind, *, exchange, max_pages, limit):
        self.calls.append((market, kind, exchange, max_pages, limit))
        if exchange == "NYS":
            raise TimeoutError("synthetic")
        return ActivityPage(
            rows=[
                ActivityRow(
                    ticker="AAA" if exchange == "NAS" else "BBB",
                    market=market,
                    exchange=exchange,
                    currency="USD",
                    turnover="100",
                    turnover_source_field="tamt",
                    price="20",
                    source_rank=4,
                )
            ],
            pages=1,
        )


def deps(project, mc, now=OPEN):
    dbpath = project / "state" / "trader.db"
    conn = db.connect(dbpath)
    db.init_schema(conn, all_migrations(discover()))
    conn.close()
    routes.install(project / "state", market=mc)
    return SimpleNamespace(
        settings=SimpleNamespace(quotes=QuotesSettings()),
        ports={},
        state=project / "state",
        conn_factory=lambda: db.connect(dbpath),
        now=lambda: now,
        markets=["US"],
    )


def test_collector_isolates_venue_failure_and_persists_for_readers(project):
    mc = Market()
    d = deps(project, mc)
    assert hasattr(jobs, "market_activity_body"), "persistent collector required"
    result = jobs.market_activity_body(d)
    with d.conn_factory() as conn:
        got = api.activity_snapshot(conn, "US", asof=OPEN)
    assert result["US"]["received_at"] == OPEN.isoformat()
    assert {r["ticker"] for r in got["rankings"]["turnover"]} == {"AAA", "BBB"}
    assert len(got["errors"]) == 3
    assert got["coverage"]["failed"] == ["NYS:turnover", "NYS:gain", "NYS:loss"]
    assert got["rankings"]["turnover"][0]["growth"]["status"] == "unavailable"
    from trader.kernel.session.tooling import compact_context

    row = compact_context({"market_activity": got})["market_activity"]["rankings"]["turnover"][0]
    assert row["turnover_unit"] == "unknown" and row["turnover_basis"] == "source_reported"
    assert row["turnover_source_field"] == "tamt" and row["turnover"] == "100"
    assert len(mc.calls) == 9 and all(call[3:] == (2, 100) for call in mc.calls)
    spec = {j.id: j for j in FEATURE.jobs}["market_activity"]
    assert spec.interval_s == 30 and spec.enabled(d.settings)


def test_collector_closed_or_disabled_never_refreshes_old_counters(project):
    mc = Market()
    d = deps(project, mc, now=datetime(2026, 11, 7, 15, 0, tzinfo=UTC))
    assert hasattr(jobs, "market_activity_body"), "persistent collector required"
    assert jobs.market_activity_body(d)["US"]["skipped"] == "outside_poll_window"
    assert mc.calls == []
    d = deps(project, mc)
    d.settings.quotes = QuotesSettings(activity_enabled=False)
    assert jobs.market_activity_body(d)["skipped"] == "disabled"
    assert mc.calls == []


def test_read_route_does_not_construct_or_call_broker(project, monkeypatch):
    from tests.features.conftest import web_client

    c = web_client(project, [FEATURE], now=OPEN)

    def forbidden(*args, **kwargs):
        raise AssertionError("GET must read stored data only")

    monkeypatch.setattr(routes, "runtime_for", forbidden)
    conn = db.connect(project / "state" / "trader.db")
    put(conn, OPEN, "123")
    conn.close()
    response = c.get("/api/quotes/activity", params={"market": "US"})
    assert response.status_code == 200
    assert response.json()["rankings"]["turnover"][0]["turnover"] == "123"
    assert c.get("/api/quotes/activity", params={"market": "ALL"}).status_code == 400
