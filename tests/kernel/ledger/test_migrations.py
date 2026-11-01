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
