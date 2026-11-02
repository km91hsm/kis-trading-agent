"""ingest 테스트 공용 픽스처."""

import pytest
import respx

from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations


@pytest.fixture(autouse=True)
def _reset_ingest_state():
    """프로세스 전역 상태(멈춘 절 수·거시 메모)를 테스트마다 비운다."""
    from trader.features.ingest import inputs

    inputs.reset_memo()
    yield
    inputs.reset_memo()


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
