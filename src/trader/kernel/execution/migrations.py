"""execution 원장 v5: 주문·승인·리스크 판정·실행 키·매수 예약·포지션 스냅샷·계좌 NAV."""

from __future__ import annotations

from trader.kernel.execution.types import ORIGIN_CHECK
from trader.kernel.ledger.migrations import Migration

_ORIGINS_SQL = ",".join(f"'{o}'" for o in ORIGIN_CHECK)

EXECUTION = Migration(
    5,
    "execution",
    statements=(
        f"""CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            decision_id INTEGER,
            account_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            market TEXT NOT NULL,
            side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
            qty INTEGER NOT NULL,
            order_type TEXT NOT NULL,
            price TEXT,
            status TEXT NOT NULL CHECK(status IN
                ('accepted','rejected','failed','unknown','filled','partial','cancelled','expired')),
            order_no TEXT,
            message TEXT,
            mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            amount TEXT,
            org_no TEXT,
            asset TEXT NOT NULL DEFAULT 'stock' CHECK(asset IN ('stock','fo')),
            origin TEXT NOT NULL DEFAULT 'llm' CHECK(origin IN ({_ORIGINS_SQL}))
        )""",
        "CREATE INDEX ix_orders_account_created ON orders(account_id, created_at)",
        "CREATE INDEX ix_orders_decision ON orders(decision_id)",
        """CREATE TABLE approvals (
            id INTEGER PRIMARY KEY,
            decision_id INTEGER NOT NULL DEFAULT 0,
            account_id TEXT NOT NULL,
            order_json TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK(status IN ('pending','approved','rejected','expired','failed')),
            requested_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            decided_at TEXT,
            fingerprint TEXT NOT NULL,
            stage TEXT,
            exec_key TEXT,
            origin TEXT,
            quote_price TEXT
        )""",
        """CREATE TABLE risk_checks (
            id INTEGER PRIMARY KEY,
            decision_id INTEGER NOT NULL,
            account_id TEXT NOT NULL,
            approved INTEGER NOT NULL,
            adjusted_qty INTEGER,
            reasons_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE exec_locks (
            key TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            order_id INTEGER,
            approval_id INTEGER,
            sent_at TEXT
        )""",
        """CREATE TABLE buy_reservations (
            key TEXT PRIMARY KEY REFERENCES exec_locks(key) ON DELETE CASCADE,
            account_id TEXT NOT NULL,
            market TEXT NOT NULL,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            amount TEXT NOT NULL,
            bound_at TEXT
        )""",
        "CREATE INDEX ix_buy_reservations_acct ON buy_reservations(account_id, market)",
        """CREATE TABLE positions_snapshot (
            id INTEGER PRIMARY KEY,
            account_id TEXT NOT NULL,
            date TEXT NOT NULL,
            market TEXT NOT NULL,
            asset TEXT NOT NULL DEFAULT 'stock',
            ticker TEXT NOT NULL,
            qty INTEGER NOT NULL,
            avg_price TEXT,
            current_price TEXT,
            eval_amount TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(account_id, date, market, asset, ticker)
        )""",
        """CREATE TABLE IF NOT EXISTS fills (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            account_id TEXT NOT NULL,
            qty INTEGER NOT NULL,
            price TEXT NOT NULL,
            filled_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS ix_fills_order ON fills(order_id)",
        """CREATE TABLE account_nav (
            id INTEGER PRIMARY KEY,
            account_id TEXT NOT NULL,
            date TEXT NOT NULL,
            market TEXT NOT NULL,
            nav TEXT NOT NULL,
            cash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(account_id, date, market)
        )""",
    ),
    owner="kernel/execution",
)
