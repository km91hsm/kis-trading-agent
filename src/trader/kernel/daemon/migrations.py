"""daemon 원장 v8: 웹·CLI 가 데몬에 맡기는 작업 요청 큐(job_requests)."""

from __future__ import annotations

from trader.kernel.ledger.migrations import Migration

QUEUE = Migration(
    8,
    "daemon_job_requests",
    statements=(
        """CREATE TABLE job_requests (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            params TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','running','ok','error','skipped')),
            source TEXT,
            requested_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            progress TEXT,
            error TEXT,
            result TEXT,
            priority INTEGER NOT NULL DEFAULT 0
        )""",
        "CREATE INDEX ix_job_requests_status ON job_requests(status, id)",
        "CREATE INDEX ix_job_requests_prio ON job_requests(status, priority, id)",
    ),
    owner="kernel",
)
