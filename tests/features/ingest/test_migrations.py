"""원장 v3 news_items."""

from trader.kernel.ledger import db

COLUMNS = [
    "id", "market", "source", "query", "title", "url", "url_hash",
    "published_at", "received_at", "summary", "tickers_json", "lang", "novel", "severity",
]  # fmt: skip


def test_news_items_schema_columns(conn):
    rows = conn.execute("PRAGMA table_info(news_items)").fetchall()
    assert [r[1] for r in rows] == COLUMNS


def test_version_includes_ingest_v3(conn):
    assert db.current_version(conn) >= 3
