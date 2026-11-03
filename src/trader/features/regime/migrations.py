"""regime 원장 마이그레이션 (전역 버전 10: 시장 세션 제안 `market_views`)."""

from trader.kernel.ledger.migrations import Migration

MIGRATIONS = (
    Migration(
        10,
        "regime_market_views",
        owner="regime",
        statements=(
            """CREATE TABLE market_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                market TEXT NOT NULL,
                stance TEXT,
                hedge_weight REAL NOT NULL,
                leveraged INTEGER NOT NULL DEFAULT 0,
                confidence REAL,
                horizon_days INTEGER NOT NULL,
                rationale TEXT,
                created_at TEXT NOT NULL,
                matured_at TEXT,
                index_return REAL,
                hit INTEGER,
                UNIQUE(date, market)
            )""",
        ),
    ),
)
