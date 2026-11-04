"""fo 원장 마이그레이션 (전역 버전 14: 포지션 상태기계·주문 다리·결정·NAV/보유 스냅샷).

옛 v33(Phase 26)과 v34(`request_id`)를 하나로 접었다. `orders.asset`·`accounts.asset` 열은 이미
있어 ALTER 는 없다."""

from trader.kernel.ledger.migrations import Migration

MIGRATIONS = (
    Migration(
        14,
        "fo",
        owner="fo",
        statements=(
            """CREATE TABLE IF NOT EXISTS fo_positions (
                id INTEGER PRIMARY KEY,
                account_id TEXT NOT NULL, code TEXT NOT NULL, kind TEXT NOT NULL,
                contract_side TEXT NOT NULL, direction TEXT NOT NULL, vehicle TEXT NOT NULL,
                status TEXT NOT NULL,
                qty_target INTEGER NOT NULL DEFAULT 0, qty_held INTEGER NOT NULL DEFAULT 0,
                leverage_used REAL, stop_pt TEXT, stop_fut TEXT, tp_pt TEXT, approved_mult REAL,
                premium_stop TEXT, entry_premium TEXT, nav_at_open TEXT,
                realized_loss TEXT NOT NULL DEFAULT '0', fees TEXT NOT NULL DEFAULT '0',
                active_reduce_leg_id INTEGER, horizon_until TEXT, last_trade_date TEXT,
                decision_date TEXT, round INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 0,
                opened_at TEXT, closed_at TEXT, note TEXT, created_at TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS ix_fo_positions_account_status "
            "ON fo_positions(account_id, status)",
            # 실행 키 = (position, intent, round, attempt) UNIQUE. request_id 는 주문 다리 ↔ 결정
            # 봉투 연결 — 제출 재개가 '이 결정의 다리가 이미 있는가' 를 시각 추정 없이 판정한다
            """CREATE TABLE IF NOT EXISTS fo_order_legs (
                id INTEGER PRIMARY KEY,
                position_id INTEGER NOT NULL, account_id TEXT NOT NULL, intent TEXT NOT NULL,
                round INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL, order_id INTEGER, order_no TEXT,
                qty INTEGER NOT NULL, filled_qty INTEGER NOT NULL DEFAULT 0,
                price TEXT, order_type TEXT, sent_at TEXT, accepted_at TEXT,
                created_at TEXT NOT NULL, request_id TEXT,
                UNIQUE(position_id, intent, round, attempt)
            )""",
            "CREATE INDEX IF NOT EXISTS ix_fo_legs_account_status "
            "ON fo_order_legs(account_id, status)",
            "CREATE INDEX IF NOT EXISTS ix_fo_legs_request "
            "ON fo_order_legs(account_id, request_id)",
            """CREATE TABLE IF NOT EXISTS fo_decisions (
                id INTEGER PRIMARY KEY,
                date TEXT NOT NULL, account_id TEXT, position_id INTEGER,
                round INTEGER NOT NULL DEFAULT 0, request_id TEXT NOT NULL,
                intent TEXT NOT NULL, direction TEXT NOT NULL, vehicle TEXT NOT NULL,
                target_leverage REAL, leverage_used REAL, approved_mult REAL, confidence REAL,
                index_at_decision TEXT, quote_asof TEXT, data TEXT NOT NULL,
                matured_at TEXT, index_return REAL, direction_hit INTEGER, contract_pnl TEXT,
                cost TEXT, created_at TEXT NOT NULL,
                UNIQUE(date, account_id, position_id, round)
            )""",
            # 아침 결정은 account_id·position_id 가 NULL 이라(UNIQUE 는 NULL 을 서로 다르게 본다)
            # 식 인덱스로 날짜·라운드당 하나를 강제한다
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_fo_decisions_key ON fo_decisions("
            "date, COALESCE(account_id, ''), COALESCE(position_id, 0), round)",
            """CREATE TABLE IF NOT EXISTS fo_nav_snapshot (
                account_id TEXT NOT NULL, date TEXT NOT NULL,
                nav TEXT, cash TEXT, margin_total TEXT, add_margin TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (account_id, date)
            )""",
            """CREATE TABLE IF NOT EXISTS fo_positions_snapshot (
                account_id TEXT NOT NULL, date TEXT NOT NULL, code TEXT NOT NULL,
                kind TEXT NOT NULL, contract_side TEXT NOT NULL, qty INTEGER NOT NULL,
                avg TEXT, current TEXT, delta REAL, delta_notional TEXT, eval TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (account_id, date, code)
            )""",
        ),
    ),
)
