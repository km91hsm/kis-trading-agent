"""scan 원장 마이그레이션 (전역 버전 11: 테마 열기 기록 `theme_heat`)."""

from trader.kernel.ledger.migrations import Migration

MIGRATIONS = (
    Migration(
        11,
        "scan_theme_heat",
        owner="scan",
        statements=(
            """CREATE TABLE theme_heat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                date TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                theme TEXT NOT NULL,
                rank INTEGER NOT NULL,
                score REAL NOT NULL,
                catalyst_ok INTEGER NOT NULL,
                tickers_json TEXT NOT NULL,
                components_json TEXT NOT NULL,
                fwd_ret_5d REAL,
                UNIQUE(market, date, theme)
            )""",
        ),
    ),
)
