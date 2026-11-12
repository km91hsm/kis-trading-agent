import pytest

from trader.kernel.ledger.db import LedgerError
from trader.kernel.ledger.migrations import KERNEL, Migration, check


def test_duplicate_version_rejected():
    with pytest.raises(LedgerError, match="버전 2 중복: x, y"):
        check([Migration(1, "a"), Migration(2, "b", owner="x"), Migration(2, "c", owner="y")])


def test_gap_rejected():
    with pytest.raises(LedgerError, match="연속"):
        check([Migration(1, "a"), Migration(3, "b")])


def test_must_start_at_one():
    with pytest.raises(LedgerError, match="연속"):
        check([Migration(2, "a")])


def test_not_ascending_rejected():
    with pytest.raises(LedgerError, match="연속"):
        check([Migration(2, "b"), Migration(1, "a")])


def test_valid_and_empty_pass():
    check([Migration(1, "a"), Migration(2, "b")])
    check([])


def test_kernel_registry_is_valid_v1():
    check(KERNEL)
    assert [(m.version, m.name, m.owner) for m in KERNEL] == [(1, "kernel-core", "kernel")]


def test_all_migrations_through_v20_include_session_tables(project):
    from trader.kernel.ledger import db
    from trader.kernel.registry import discover
    from trader.kernel.registry.contracts import all_migrations

    migs = all_migrations(discover())
    assert [m.version for m in migs] == list(range(1, 21))
    v4 = next(m for m in migs if m.version == 4)
    assert v4.owner == "kernel/session"
    c = db.connect()
    db.init_schema(c, migs)
    names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    c.close()
    assert {"agent_calls", "llm_rate_limits", "research_calls", "research_policies"} <= names


def _migrated(project):
    from trader.kernel.ledger import db
    from trader.kernel.registry import discover
    from trader.kernel.registry.contracts import all_migrations

    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    return c


def test_migration_15_creates_notices_and_control_log(project):
    c = _migrated(project)
    names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master")}
    c.close()
    assert {"notices", "control_log", "ix_notices_at", "ix_control_log_at"} <= names


def test_migration_15_adds_decided_columns_to_approvals(project):
    c = _migrated(project)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(approvals)")}
    c.close()
    assert {"decided_by", "decided_channel"} <= cols
