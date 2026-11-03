"""evaluation 원장 마이그레이션 (전역 버전 13: 메모리·보정·섀도 포트폴리오·RL 섀도)."""

from trader.kernel.ledger.migrations import Migration

MIGRATIONS = (
    Migration(
        13,
        "evaluation",
        owner="evaluation",
        statements=(
            """CREATE TABLE IF NOT EXISTS memory_entries (
              id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, market TEXT NOT NULL, account_id TEXT,
              decision_id INTEGER, round INTEGER NOT NULL DEFAULT 0, decided_at TEXT NOT NULL,
              situation_vector TEXT NOT NULL, situation_summary TEXT, decision TEXT NOT NULL,
              horizon_days INTEGER NOT NULL, reflection TEXT, outcome_at TEXT,
              tier TEXT NOT NULL, importance REAL NOT NULL, created_at TEXT NOT NULL,
              price_return REAL, held_at_decision INTEGER, direction_score REAL, avoided_loss REAL,
              realized_pnl_pct REAL, post_fill_return REAL, skipped_debate INTEGER,
              UNIQUE(decision_id, round))""",
            "CREATE INDEX IF NOT EXISTS ix_memory_outcome ON memory_entries(outcome_at, market)",
            """CREATE TABLE IF NOT EXISTS calibration_runs (id INTEGER PRIMARY KEY,
              created_at TEXT NOT NULL,
              n_decisions INTEGER NOT NULL, active INTEGER NOT NULL, json TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS shadow_portfolios (id INTEGER PRIMARY KEY AUTOINCREMENT,
              market TEXT NOT NULL,
              date TEXT NOT NULL, strategy TEXT NOT NULL, weights_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              fwd_ret REAL, matured_at TEXT, partial INTEGER NOT NULL DEFAULT 0,
              def_version INTEGER NOT NULL,
              UNIQUE(market, date, strategy))""",
            """CREATE TABLE IF NOT EXISTS rl_shadow (id INTEGER PRIMARY KEY,
              decision_id INTEGER NOT NULL, account_id TEXT NOT NULL,
              created_at TEXT NOT NULL, model_version TEXT NOT NULL, obs TEXT NOT NULL,
              llm_weight REAL NOT NULL,
              proposed_weight REAL NOT NULL, applied INTEGER NOT NULL DEFAULT 0,
              UNIQUE(decision_id, account_id))""",
        ),
    ),
)
