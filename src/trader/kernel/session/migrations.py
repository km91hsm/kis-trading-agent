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


RESEARCH = Migration(
    17,
    "research-providers",
    statements=(
        "ALTER TABLE agent_calls ADD COLUMN provider TEXT NOT NULL DEFAULT 'claude'",
        """CREATE TABLE research_policies (
            research_key TEXT PRIMARY KEY,
            policy_json TEXT NOT NULL,
            policy_hash TEXT NOT NULL,
            context_hash TEXT,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE research_calls (
            id INTEGER PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT,
            policy_hash TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            schema_hash TEXT NOT NULL,
            turn_hash TEXT,
            status TEXT NOT NULL CHECK(status IN ('running','ok','error')),
            error TEXT,
            duration_ms INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            counts_toward_cap INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX ix_research_calls_created ON research_calls(provider, created_at)",
    ),
    owner="kernel/session",
)


# 도중 Codex 실패 표시(재시작 때 토론 보관 → Claude 2인 교체)와 교체 이력
RESEARCH_FAILOVER = Migration(
    18,
    "research-failover",
    statements=(
        "ALTER TABLE research_policies ADD COLUMN codex_failed_at TEXT",
        "ALTER TABLE research_policies ADD COLUMN codex_failed_reason TEXT",
        "ALTER TABLE research_policies ADD COLUMN replaced_from_hash TEXT",
        "ALTER TABLE research_policies ADD COLUMN replaced_reason TEXT",
    ),
    owner="kernel/session",
)
