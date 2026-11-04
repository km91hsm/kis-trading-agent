"""마이그레이션 레지스트리. 커널은 버전 1 만 소유하고 기능은 자기 Migration 을 뒤에 덧붙인다."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from trader.kernel.ledger.db import LedgerError


@dataclass(frozen=True)
class Migration:
    """한 스키마 버전. statements 를 먼저, 이어서 fn 을 같은 트랜잭션에서 실행한다."""

    version: int
    name: str
    statements: tuple[str, ...] = ()
    fn: Callable[[sqlite3.Connection], None] | None = None
    owner: str = "kernel"


def check(migs: Sequence[Migration]) -> None:
    """버전이 1부터 빠짐없이 오름차순으로 이어지는지 검사한다. 위반 시 LedgerError."""
    owners: dict[int, list[str]] = {}
    for m in migs:
        owners.setdefault(m.version, []).append(m.owner)
    for version, who in owners.items():
        if len(who) > 1:
            raise LedgerError(f"마이그레이션 버전 {version} 중복: {', '.join(who)}")
    versions = [m.version for m in migs]
    if versions != list(range(1, len(versions) + 1)):
        raise LedgerError(f"마이그레이션 버전은 1부터 연속 오름차순이어야 합니다: {versions}")


KERNEL: list[Migration] = [
    Migration(
        1,
        "kernel-core",
        (
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)",
            """CREATE TABLE control (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE runs (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                market TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL CHECK(status IN ('running','ok','error','skipped')),
                error TEXT,
                result_json TEXT
            )""",
            "CREATE INDEX ix_runs_kind_started ON runs(kind, started_at)",
        ),
    )
]


# 웹 API(M11): 알림·제어 변경 기록 + 승인 결정자. KERNEL(v1) 목록이 아닌 커널 모듈 목록에 들어간다.
WEB = Migration(
    15,
    "web",
    statements=(
        """CREATE TABLE IF NOT EXISTS notices (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            kind TEXT NOT NULL,
            account_id TEXT,
            text TEXT NOT NULL,
            channel TEXT NOT NULL,
            ok INTEGER NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS ix_notices_at ON notices(at)",
        """CREATE TABLE IF NOT EXISTS control_log (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            channel TEXT NOT NULL,
            actor TEXT,
            key TEXT NOT NULL,
            old TEXT,
            new TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS ix_control_log_at ON control_log(at)",
        "ALTER TABLE approvals ADD COLUMN decided_by TEXT",
        "ALTER TABLE approvals ADD COLUMN decided_channel TEXT",
    ),
    owner="kernel/web",
)
