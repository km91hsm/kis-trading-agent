"""kernel/execution 테스트 공용 픽스처."""

import pytest

from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()
