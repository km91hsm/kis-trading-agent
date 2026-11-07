"""KIS 시장 활동의 수신 이력. 기존 관측은 수정·삭제하지 않는다."""

from trader.kernel.ledger.migrations import Migration

MIGRATIONS = (
    Migration(
        16,
        "quotes-market-activity",
        (
            """CREATE TABLE market_activity_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL CHECK(market IN ('KR','US')),
                received_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )""",
            "CREATE INDEX ix_activity_market_received "
            "ON market_activity_batches(market, received_at)",
            """CREATE TABLE market_activity_partitions (
                batch_id INTEGER NOT NULL REFERENCES market_activity_batches(id),
                market TEXT NOT NULL CHECK(market IN ('KR','US')),
                label TEXT NOT NULL,
                ok INTEGER NOT NULL CHECK(ok IN (0,1)),
                received_at TEXT NOT NULL,
                PRIMARY KEY (batch_id, label)
            )""",
            "CREATE INDEX ix_activity_partition_latest "
            "ON market_activity_partitions(market,label,received_at DESC,batch_id DESC)",
            "CREATE INDEX ix_activity_partition_success "
            "ON market_activity_partitions(market,label,ok,received_at DESC,batch_id DESC)",
            """CREATE TRIGGER activity_partition_no_update
                BEFORE UPDATE ON market_activity_partitions
                BEGIN SELECT RAISE(ABORT, 'market activity partitions are append-only'); END""",
            """CREATE TRIGGER activity_partition_no_delete
                BEFORE DELETE ON market_activity_partitions
                BEGIN SELECT RAISE(ABORT, 'market activity partitions are append-only'); END""",
            """CREATE TRIGGER activity_no_update BEFORE UPDATE ON market_activity_batches
                BEGIN SELECT RAISE(ABORT, 'market activity is append-only'); END""",
            """CREATE TRIGGER activity_no_delete BEFORE DELETE ON market_activity_batches
                BEGIN SELECT RAISE(ABORT, 'market activity is append-only'); END""",
        ),
        owner="quotes",
    ),
)
