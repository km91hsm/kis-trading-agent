"""accounts 원장 마이그레이션 (전역 버전 2)."""

from trader.kernel.ledger.migrations import Migration

MIGRATIONS = (
    Migration(
        2,
        "accounts",
        (
            """CREATE TABLE accounts (
                id TEXT PRIMARY KEY,
                env TEXT NOT NULL CHECK(env IN ('paper','real')),
                asset TEXT NOT NULL CHECK(asset IN ('stock','fo')),
                acnt_prdt_cd TEXT NOT NULL,
                markets_json TEXT NOT NULL,
                yaml_mode TEXT NOT NULL CHECK(yaml_mode IN ('auto','approve','off')),
                first_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE account_modes (
                account_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL CHECK(mode IN ('auto','approve','off')),
                live_confirmed_json TEXT,
                updated_at TEXT NOT NULL
            )""",
        ),
        owner="accounts",
    ),
)
