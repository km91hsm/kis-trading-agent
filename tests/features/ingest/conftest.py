"""ingest 테스트 공용 픽스처. Task 12 가 autouse 전역 초기화를 여기에 더한다."""

import pytest
import respx

from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations


@pytest.fixture
def respx_mock():
    with respx.mock(assert_all_called=False) as m:
        yield m


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()
