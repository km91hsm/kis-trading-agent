import logging

import pytest

from trader.features.accounts import brokers
from trader.features.accounts.settings import Account
from trader.kernel.broker import factory
from trader.kernel.broker.creds import key_fingerprint
from trader.kernel.config.secrets import Secrets

SECRETS = Secrets.model_validate({
    "KIS_REAL_APP_KEY": "shared-k", "KIS_REAL_APP_SECRET": "shared-s", "KIS_REAL_CANO": "11111111",
    "KIS_HTS_ID": "me", "KIS_REAL_2_APP_KEY": "k2", "KIS_REAL_2_APP_SECRET": "s2",
    "KIS_REAL_2_CANO": "22222222",
})


def _acct(id_, env="real", **kw):
    return Account(id=id_, env=env, cano="00000000", mode="off", **kw)


@pytest.fixture(autouse=True)
def _reset(project):
    factory.reset_clients()
    yield
    factory.reset_clients()


def test_brokers_use_per_key_clients_and_warn_on_shared_key(caplog):
    accts = [_acct("real-main"), _acct("real-2"), _acct("real-3")]
    with caplog.at_level(logging.WARNING, logger="trader.accounts"):
        built = {b.account.id: b for b in brokers.build_brokers(SECRETS, accts)}
    assert built["real-2"].client is not built["real-main"].client
    fp = key_fingerprint("k2")
    assert built["real-2"].client.auth.cache_path.name == f"kis_token_real-{fp}.json"
    assert built["real-3"].client is built["real-main"].client
    assert sum("real-3" in r.message and "KIS_REAL_3_" in r.message for r in caplog.records) == 1


def test_fo_shared_key_is_info_not_warning(caplog):
    fo = _acct("paper-fo", env="paper", asset="fo", acnt_prdt_cd="03", markets=["KR"])
    stock = _acct("paper-main", env="paper")
    sec = Secrets(KIS_PAPER_APP_KEY="pk", KIS_PAPER_APP_SECRET="ps")
    with caplog.at_level(logging.INFO, logger="trader.accounts"):
        out = brokers.build_brokers(sec, [stock, fo])
    assert [b.account.id for b in out] == ["paper-main"]  # fo_product 없음 → 건너뜀(경고)
    assert any(
        r.levelno == logging.INFO and "paper-fo" in r.message and "같은 앱키" in r.message
        for r in caplog.records
    )
    assert any(
        r.levelno == logging.WARNING and "paper-fo" in r.message and "건너뜀" in r.message
        for r in caplog.records
    )


def test_missing_key_account_is_skipped_with_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="trader.accounts"):
        out = brokers.build_brokers(Secrets(), [_acct("paper-main", env="paper")])
    assert out == [] and any(
        "건너뜀" in r.message and "KIS_PAPER_APP_KEY" in r.message for r in caplog.records
    )
    assert not any("secret" in r.message.lower() and "ps" in r.message for r in caplog.records)


def test_warnings_never_leak_key_values(caplog):
    sec = Secrets(KIS_PAPER_APP_KEY="pk-SECRET", KIS_PAPER_APP_SECRET="ps-SECRET")
    accts = [_acct("paper-main", env="paper"), _acct("paper-2", env="paper"),
             _acct("real-main")]  # 같은 키 공유 경고 + 키 없는 계좌 건너뜀 경고
    with caplog.at_level(logging.DEBUG, logger="trader.accounts"):
        brokers.build_brokers(sec, accts)
    assert any("같은 앱키" in r.message for r in caplog.records)
    assert any("건너뜀" in r.message for r in caplog.records)
    assert "pk-SECRET" not in caplog.text and "ps-SECRET" not in caplog.text


def test_market_falls_back_to_first_real_stock_account_key():
    only = Secrets.model_validate({"KIS_REAL_2_APP_KEY": "k2", "KIS_REAL_2_APP_SECRET": "s2"})
    assert brokers.market_creds(only, [_acct("real-2")]).app_key == "k2"
    assert brokers.market_creds(SECRETS, [_acct("real-2")]).app_key == "shared-k"
    fo_only = Secrets.model_validate(
        {"KIS_REAL_FO_APP_KEY": "kf", "KIS_REAL_FO_APP_SECRET": "sf"}
    )
    fo_acct = _acct("real-fo", asset="fo", acnt_prdt_cd="03", markets=["KR"])
    assert brokers.market_creds(fo_only, [fo_acct]).app_key is None


def test_market_creds_loads_accounts_when_not_given(project):
    (project / "config" / "accounts.yaml").write_text(
        'accounts:\n  - {id: real-2, env: real, cano: "00000000", markets: [KR], mode: "off"}\n',
        encoding="utf-8",
    )
    only = Secrets.model_validate({"KIS_REAL_2_APP_KEY": "k2", "KIS_REAL_2_APP_SECRET": "s2"})
    assert brokers.market_creds(only).app_key == "k2"
