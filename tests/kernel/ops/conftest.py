"""kernel/ops 테스트 공용 픽스처: 원장·커널 설정·OpsContext 조립."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from trader.kernel.config import LlmSettings
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

NOW = datetime(2026, 10, 23, 8, 30, tzinfo=UTC)  # 17:30 KST 금요일


class FakeAccounts:
    """`ports["accounts"]` 자리: 합성 계좌 목록만 돌려준다."""

    def __init__(self, accounts: list) -> None:
        self._accounts = list(accounts)

    def accounts(self) -> list:
        return list(self._accounts)


def account(id_: str, markets: list[str], asset: str = "stock", env: str = "paper"):
    return SimpleNamespace(id=id_, env=env, asset=asset, markets=markets)


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def settings(project):
    """커널 설정(llm·markets). 사용률 관측 파일은 project 안(없으면 관측 없음)."""
    usage_file = project / "state" / "rate_limits.json"
    return SimpleNamespace(llm=LlmSettings(usage_file=str(usage_file)), markets=["KR", "US"])


@pytest.fixture
def make_ctx(conn, settings, project):
    from trader.kernel.ops import OpsContext

    def make(*, now=NOW, ports=None, jobs=(), features=()):
        return OpsContext(
            conn=conn,
            settings=settings,
            state=project / "state",
            now=now,
            ports=dict(ports or {}),
            jobs=list(jobs),
            features=list(features),
        )

    return make
