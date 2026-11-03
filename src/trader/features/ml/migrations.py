"""ml 원장 마이그레이션 (전역 버전 12: 시점 기준 유니버스 `universe_members`)."""

from trader.kernel.ledger.migrations import Migration

MIGRATIONS = (
    Migration(
        12,
        "universe_members",
        owner="ml",
        statements=(
            """CREATE TABLE universe_members (
                market TEXT NOT NULL,
                ticker TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'universe',
                PRIMARY KEY (market, ticker, first_seen)
            )""",
        ),
    ),
)
