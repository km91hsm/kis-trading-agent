"""accounts 기능 테스트 공용 헬퍼."""

import pytest

from trader.features.accounts.settings import Account
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations


def make_account(id: str, env: str, mode: str, markets=None, **kw) -> Account:
    """합성 계좌(cano 자리표시자, markets 기본 [KR])."""
    return Account(id=id, env=env, cano="00000000", mode=mode, markets=markets or ["KR"], **kw)


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()
