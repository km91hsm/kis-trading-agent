"""ingest 원장 마이그레이션 (전역 버전 3)."""

from trader.kernel.ledger.migrations import Migration

MIGRATIONS = (
    Migration(
        3,
        "ingest",
        (
            """CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                source TEXT NOT NULL,
                query TEXT,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                url_hash TEXT NOT NULL UNIQUE,
                published_at TEXT,
                received_at TEXT NOT NULL,
                summary TEXT,
                tickers_json TEXT NOT NULL DEFAULT '[]',
                lang TEXT,
                novel INTEGER,
                severity TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_news_market_pub ON news_items(market, published_at)",
            "CREATE INDEX IF NOT EXISTS ix_news_market_received ON news_items(market, received_at)",
        ),
        None,
        "ingest",
    ),
)
