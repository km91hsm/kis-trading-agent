import logging

from tests.features.accounts.conftest import make_account
from trader.features.accounts import api
from trader.features.accounts.ports import AccountsPortImpl
from trader.kernel.config import Secrets


def test_policy_is_effective_mode_and_paused(conn):
    real = make_account("real-1", "real", "auto")
    paper = make_account("paper-1", "paper", "approve")
    port = AccountsPortImpl([real, paper], Secrets())
    assert port.policy(conn, real) == api.effective_mode(conn, real)
    assert port.policy(conn, real)[0] == "off"  # LIVE 미확인
    assert port.policy(conn, paper) == ("approve", None)
    assert port.paused(conn) is False
    assert port.accounts() == [real, paper] and port.holiday_fn() is None


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
