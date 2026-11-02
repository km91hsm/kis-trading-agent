"""spot 테스트 공용 픽스처·상수."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.features.accounts.conftest import make_account  # noqa: F401
from trader.features.accounts.ports import AccountsPortImpl
from trader.kernel.cli.boot import build_registry
from trader.kernel.config import Secrets
from trader.kernel.config.loader import load
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

D = date(2026, 10, 9)
T0 = datetime(2026, 10, 9, 1, 0, tzinfo=UTC)
PRICE = Decimal("71500")


@pytest.fixture
def settings(project):
    """settings.yaml 이 없으면 기본값."""
    return load("settings", build_registry(discover()), path=project / "config" / "settings.yaml")


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def accounts_port():
    return AccountsPortImpl([], Secrets())
