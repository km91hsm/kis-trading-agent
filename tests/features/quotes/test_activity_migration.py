import sqlite3

import pytest

from trader.features.quotes.feature import FEATURE


def test_activity_migration_preserves_append_only_history():
    migrations = FEATURE.migrations
    assert migrations, "quotes must register its persistent activity ledger"
    c = sqlite3.connect(":memory:")
    for m in migrations:
        for statement in m.statements:
            c.execute(statement)
    c.execute(
        "INSERT INTO market_activity_batches(market,received_at,payload_json) "
        "VALUES ('US','2026-11-07T00:00:00+00:00','{}')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("UPDATE market_activity_batches SET payload_json='[]'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("DELETE FROM market_activity_batches")
    assert c.execute("SELECT count(*) FROM market_activity_batches").fetchone()[0] == 1
