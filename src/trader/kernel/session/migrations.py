"""session 원장 v4: 세션 행(agent_calls)과 구독 사용률 관측(llm_rate_limits)."""

from __future__ import annotations

from trader.kernel.ledger.migrations import Migration

SESSION = Migration(
    4,
    "session",
    statements=(
        """CREATE TABLE agent_calls (
            id INTEGER PRIMARY KEY,
            run_id INTEGER REFERENCES runs(id),
            ticker TEXT,
            market TEXT,
            agent TEXT NOT NULL,
            model TEXT,
            session_id TEXT,
            cost_usd TEXT,
            duration_ms INTEGER,
            status TEXT NOT NULL CHECK(status IN ('running','ok','error')),
            error TEXT,
            num_turns INTEGER,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX ix_agent_calls_created ON agent_calls(created_at)",
        "CREATE INDEX ix_agent_calls_agent_created ON agent_calls(agent, created_at)",
        """CREATE TABLE llm_rate_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT,
            five_hour_util REAL,
            five_hour_resets_at TEXT,
            seven_day_util REAL,
            seven_day_resets_at TEXT,
            raw_json TEXT NOT NULL
        )""",
        "CREATE INDEX ix_llm_rate_limits_observed ON llm_rate_limits(observed_at)",
    ),
    owner="kernel/session",
)
