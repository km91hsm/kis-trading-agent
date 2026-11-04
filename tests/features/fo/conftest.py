"""fo 테스트 공용 픽스처. `project` 는 루트 conftest.

계좌: `paper-fo`(모의 파생 auto) · `real-fo`(실전 파생 off) · `paper-main`(현물). 시각은 NOW
(2026-10-27 화요일 10:30 KST — 진입 창 안). `fo_ctx(**kw)` 는 뒤 Task(store·executor·engine)가
쓰는 `FoExecContext` 공장이다."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.features.accounts.conftest import make_account
from trader.features.accounts import api as accounts_api
from trader.features.fo.settings import FoCfg
from trader.features.fo.types import FoExecContext
from trader.features.regime import ports as regime_ports
from trader.kernel.broker import fo_master
from trader.kernel.broker.fake import FakeFoBroker, FakeMarket
from trader.kernel.broker.models import FoBalance, FoCash
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

NOW = datetime(2026, 10, 27, 1, 30, tzinfo=UTC)  # 10:30 KST 화요일, 진입 창 안
TODAY = date(2026, 10, 27)
ACCT = "paper-fo"
NAV = Decimal("100000000")
MASTER_PATH = (
    Path(__file__).resolve().parents[2] / "kernel" / "broker" / "fixtures" / "fo_master_sample.mst"
)


class Notes:
    """보낸 알림을 기록하는 notifier. `ret` 로 send 반환값(False = 보내지 못함)을 정한다."""

    def __init__(self, ret: bool = True):
        self.msgs: list[str] = []
        self.ret = ret

    def send(self, text: str, *, escape: bool = True) -> bool:
        self.msgs.append(text)
        return self.ret


def fo_balance(nav: Decimal | None = NAV, add_margin: Decimal | int = 0, positions=()) -> FoBalance:
    """합성 파생 잔고(주문가능 5천만원, 증거금 0)."""
    cash = FoCash(
        deposit=nav or Decimal(0),
        orderable_cash=Decimal("50000000"),
        orderable_total=Decimal("50000000"),
        margin_total=Decimal(0),
        add_margin=Decimal(add_margin),
        nav=nav,
        realized_today=Decimal(0),
        unrealized=Decimal(0),
        requested_at=NOW,
        received_at=NOW,
    )
    return FoBalance(positions=list(positions), unverified=[], cash=cash)


@pytest.fixture
def settings(project):
    """settings.yaml 이 없으면 기본값 — 단 `fo.enabled=True`."""
    s = load("settings", build_registry(discover()), path=project / "config" / "settings.yaml")
    return s.model_copy(update={"fo": FoCfg(enabled=True)})


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def fo_account():
    return make_account(ACCT, "paper", "auto", asset="fo")


@pytest.fixture
def real_fo_account():
    return make_account("real-fo", "real", "off", asset="fo")


@pytest.fixture
def stock_account():
    return make_account("paper-main", "paper", "auto", ["KR", "US"])


@pytest.fixture
def fo_balance_factory():
    return fo_balance


@pytest.fixture
def fake_fo_broker(fo_account):
    """`fake_fo_broker(**kw) -> FakeFoBroker` 공장(기본: 모의 파생 계좌·잔고 NAV 1억)."""

    def make(**kw) -> FakeFoBroker:
        base = {"balance": fo_balance(), "now": lambda: NOW}
        return FakeFoBroker(kw.pop("account", fo_account), **(base | kw))

    return make


@pytest.fixture
def fake_market():
    """`fake_market(**kw) -> FakeMarket` 공장."""

    def make(**kw) -> FakeMarket:
        return FakeMarket(**({"prices": {}, "now": lambda: NOW} | kw))

    return make


@pytest.fixture
def master():
    return fo_master.parse(MASTER_PATH.read_bytes())


@pytest.fixture
def ports(settings, fo_account, stock_account, fake_fo_broker, fake_market):
    """`Boot.ports` 모양: accounts(파생·현물 계좌 고정) + regime(실제 구현)."""
    market = fake_market()
    accounts = SimpleNamespace(
        fo_accounts=lambda: [fo_account],
        fo_brokers=lambda: {fo_account.id: fake_fo_broker()},
        accounts=lambda: [stock_account],
        brokers=lambda: [],
        market_client=lambda: market,
        policy=accounts_api.account_mode,
        paused=accounts_api.is_paused,
        holiday_fn=lambda: (lambda m, d: False),
    )
    return {
        "accounts": accounts,
        "regime": regime_ports.build_port(SimpleNamespace(settings=settings)),
    }


@pytest.fixture
def fo_ctx(settings, conn, fo_account, fake_fo_broker, fake_market, master, ports):
    """`fo_ctx(**kw) -> FoExecContext` 공장. 기본값을 `**kw` 가 덮어쓴다."""

    def make(**kw) -> FoExecContext:
        base = {
            "settings": settings,
            "accounts": [fo_account],
            "conn": conn,
            "brokers": {fo_account.id: fake_fo_broker()},
            "market_client": fake_market(),
            "master": master,
            "notifier": Notes(),
            "now": lambda: NOW,
            "dry_run": False,
            "ports": ports,
        }
        return FoExecContext(**(base | kw))

    return make
