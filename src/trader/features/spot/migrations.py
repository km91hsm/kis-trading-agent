"""spot 원장 마이그레이션 (전역 버전 6)."""

from trader.kernel.ledger.migrations import Migration

MIGRATIONS = (
    Migration(
        6,
        "spot",
        (
            """CREATE TABLE decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                market TEXT NOT NULL,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,
                target_weight TEXT,
                order_type TEXT,
                limit_price TEXT,
                stop_loss TEXT,
                take_profit TEXT,
                horizon_days INTEGER,
                confidence REAL,
                rationale TEXT,
                data_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'llm',
                skipped_debate INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(date, market, ticker)
            )""",
            """CREATE TABLE decision_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER NOT NULL REFERENCES decisions(id),
                round INTEGER NOT NULL,
                action TEXT NOT NULL,
                stop_loss TEXT,
                source TEXT,
                skipped_debate INTEGER NOT NULL DEFAULT 0,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(decision_id, round)
            )""",
            """CREATE TABLE analyst_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_date TEXT NOT NULL,
                market TEXT NOT NULL,
                ticker TEXT NOT NULL,
                role TEXT NOT NULL,
                signal TEXT,
                score REAL,
                confidence REAL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(decision_date, market, ticker, role)
            )""",
            """CREATE TABLE debate_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_date TEXT NOT NULL,
                market TEXT NOT NULL,
                ticker TEXT NOT NULL,
                round INTEGER NOT NULL,
                side TEXT NOT NULL,
                stance TEXT,
                conviction REAL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(decision_date, market, ticker, round, side)
            )""",
            """CREATE TABLE ticker_names (
                market TEXT NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT NOT NULL,
                source TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (market, ticker)
            )""",
            """CREATE TABLE ticker_exchanges (
                market TEXT NOT NULL,
                ticker TEXT NOT NULL,
                exchange TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (market, ticker)
            )""",
        ),
        owner="spot",
    ),
)
