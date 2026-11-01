import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from trader.kernel.broker.auth import KisAuth
from trader.kernel.broker.client import KisClient, RateLimiter
from trader.kernel.broker.models import FoProduct

FIXTURES = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)

# 옛 settings 기본값(fo.products) 그대로의 파생 상품 리터럴
FO_MINI = FoProduct(
    name="미니코스피200",
    futures_prefix=("A05", "105"),
    call_prefix=("B05", "205"),
    put_prefix=("C05", "305"),
    multiplier=50000,
    futures_tick=Decimal("0.02"),
    option_ticks=[("3", "0.01"), ("10", "0.02"), ("inf", "0.05")],  # type: ignore[list-item]
)
FO_REGULAR = FoProduct(
    name="코스피200",
    futures_prefix=("A01", "101"),
    call_prefix=("B01", "201"),
    put_prefix=("C01", "301"),
    multiplier=250000,
    futures_tick=Decimal("0.05"),
    option_ticks=[("10", "0.01"), ("inf", "0.05")],  # type: ignore[list-item]
)
FO_PRODUCT = FO_MINI


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def respx_mock():
    with respx.mock(assert_all_called=False) as m:
        yield m


def _client(env: str, project: Path) -> KisClient:
    auth = KisAuth(env, "key", "secret", clock=lambda: T0, state=project / "state")
    # 토큰은 캐시 파일로 미리 깔아 발급 호출을 생략한다
    (project / "state" / f"kis_token_{env}.json").write_text(
        json.dumps({"access_token": "tok", "expires_at": "2099-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    limiter = RateLimiter(10_000, clock=lambda: 0.0, sleep=lambda s: None)
    return KisClient(env, auth, limiter=limiter, http=httpx.Client(timeout=5))


@pytest.fixture
def paper_client(project, respx_mock):
    return _client("paper", project)


@pytest.fixture
def real_client(project, respx_mock):
    return _client("real", project)


def make_account(id: str, env: str, markets: list[str] | None = None):
    from trader.kernel.broker.models import BrokerAccount

    return BrokerAccount(id=id, env=env, cano="00000000", markets=markets or ["KR"])


@pytest.fixture(autouse=True)
def _reset_factory_clients():
    """factory 의 프로세스 캐시가 테스트 사이로 새지 않게 앞뒤로 비운다."""
    from trader.kernel.broker.factory import reset_clients

    reset_clients()
    yield
    reset_clients()
