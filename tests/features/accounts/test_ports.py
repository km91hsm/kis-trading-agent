import logging
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from tests.features.accounts.conftest import make_account
from trader.features.accounts import api
from trader.features.accounts.ports import AccountsPortImpl
from trader.kernel.broker.fake import FakeMarket
from trader.kernel.broker.fo import KisFoBroker
from trader.kernel.broker.models import FoProduct
from trader.kernel.config import Secrets


def test_policy_is_effective_mode_and_paused(conn):
    real = make_account("real-1", "real", "auto")
    paper = make_account("paper-1", "paper", "approve")
    port = AccountsPortImpl([real, paper], Secrets())
    assert port.policy(conn, real) == api.effective_mode(conn, real)
    assert port.policy(conn, real)[0] == "off"  # LIVE 미확인
    assert port.policy(conn, paper) == ("approve", None)
    assert port.paused(conn) is False
    assert port.accounts() == [real, paper]


def test_brokers_skip_accounts_without_keys(caplog):
    a = make_account("paper-1", "paper", "approve")
    b = make_account("paper-2", "paper", "approve")
    s2 = Secrets(**{"KIS_PAPER_2_APP_KEY": "k2", "KIS_PAPER_2_APP_SECRET": "s2"})
    with caplog.at_level(logging.WARNING, logger="trader.accounts"):
        brokers = AccountsPortImpl([a, b], s2).brokers()
    assert len(brokers) == 1
    assert any("paper-1" in r.message and "건너뜀" in r.message for r in caplog.records)
    assert AccountsPortImpl([a, b], Secrets()).brokers() == []
    assert AccountsPortImpl([a], Secrets()).market_client() is None  # 시세 키 없음


def test_port_is_stock_only_and_real_fo_policy_off(conn):
    """현물 레인 포트는 현물 계좌만 내놓고, 정책은 `account_mode`(실전 파생은 항상 off)를 쓴다."""
    stock = make_account("paper-1", "paper", "approve")
    fo = make_account("real-fo", "real", "approve", asset="fo")
    port = AccountsPortImpl([stock, fo], Secrets())
    assert port.accounts() == [stock]
    assert port.policy(conn, fo) == api.account_mode(conn, fo)
    assert port.policy(conn, fo)[0] == "off"
    s = Secrets(**{"KIS_REAL_FO_APP_KEY": "k", "KIS_REAL_FO_APP_SECRET": "s"})
    assert AccountsPortImpl([fo], s).brokers() == []  # 파생 계좌 브로커는 만들지 않는다


def test_holiday_fn_kr_uses_market_client_and_us_uses_calendar(monkeypatch, caplog):
    port = AccountsPortImpl([make_account("paper-1", "paper", "approve")], Secrets())
    market = FakeMarket(holidays={date(2026, 10, 9)})
    monkeypatch.setattr(port, "market_client", lambda: market)
    fn = port.holiday_fn()
    assert fn("KR", date(2026, 10, 9)) is True and fn("KR", date(2026, 10, 8)) is False
    calls = len(market.calls)
    assert fn("US", date(2026, 12, 25)) is True and fn("US", date(2026, 12, 24)) is False
    assert len(market.calls) == calls  # US 는 시세 클라이언트를 건드리지 않는다

    class Boom:
        def is_holiday(self, d):
            raise RuntimeError("KIS 장애")

    monkeypatch.setattr(port, "market_client", lambda: Boom())
    with caplog.at_level(logging.WARNING, logger="trader.accounts"):
        assert port.holiday_fn()("KR", date(2026, 10, 9)) is False  # 실패 = 개장
    assert any("휴장일" in r.message for r in caplog.records)
    monkeypatch.setattr(port, "market_client", lambda: None)
    assert port.holiday_fn()("KR", date(2026, 10, 9)) is False


_FO_PRODUCT = FoProduct(
    futures_prefix=("A05",),
    call_prefix=("B05",),
    put_prefix=("C05",),
    multiplier=50000,
    futures_tick=Decimal("0.02"),
    option_ticks=[("3", "0.01"), ("10", "0.02"), ("inf", "0.05")],
    name="미니코스피200",
)
_FO_SECRETS = {
    "KIS_PAPER_FO_APP_KEY": "k1",
    "KIS_PAPER_FO_APP_SECRET": "s1",
    "KIS_PAPER_MAIN_APP_KEY": "k2",
    "KIS_PAPER_MAIN_APP_SECRET": "s2",
}


def test_port_splits_fo_accounts_and_builds_fo_brokers():
    stock = make_account("paper-main", "paper", "auto")
    fo = make_account("paper-fo", "paper", "auto", asset="fo")
    settings = SimpleNamespace(fo=SimpleNamespace(product_cfg=lambda: _FO_PRODUCT))
    port = AccountsPortImpl([stock, fo], Secrets(**_FO_SECRETS), settings)
    assert [a.id for a in port.accounts()] == ["paper-main"]
    assert [a.id for a in port.fo_accounts()] == ["paper-fo"]
    brokers = port.fo_brokers()
    assert set(brokers) == {"paper-fo"}
    assert isinstance(brokers["paper-fo"], KisFoBroker)
    assert all(b.account.id != "paper-fo" for b in port.brokers())  # 현물 접근자는 fo 제외


def test_port_fo_brokers_empty_without_fo_settings(caplog):
    fo = make_account("paper-fo", "paper", "auto", asset="fo")
    port = AccountsPortImpl([fo], Secrets(**_FO_SECRETS), SimpleNamespace())
    with caplog.at_level(logging.WARNING, logger="trader.accounts"):
        assert port.fo_brokers() == {}
    assert any("settings.fo 없음" in r.message for r in caplog.records)
    assert AccountsPortImpl([fo], Secrets(**_FO_SECRETS)).fo_brokers() == {}  # settings 자체가 없다
