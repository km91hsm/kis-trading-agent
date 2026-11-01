import sqlite3
from collections.abc import Iterator

import pytest


@pytest.fixture
def conn(project) -> Iterator[sqlite3.Connection]:
    from trader.kernel.ledger import db
    from trader.kernel.ledger.migrations import KERNEL

    c = db.connect()
    db.init_schema(c, KERNEL)
    yield c
    c.close()
