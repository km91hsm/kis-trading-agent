# tests/features/accounts/test_settings.py
from decimal import Decimal

import pytest
import yaml

from trader.features.accounts import settings as S
from trader.kernel.config import ConfigError
from trader.kernel.config.secrets import Secrets


def acc(id, env, mode, markets=("KR", "US"), **extra):
    d = {
        "id": id,
        "env": env,
        "cano": "00000000",
        "acnt_prdt_cd": "01",
        "markets": list(markets),
        "mode": mode,
    }
    d.update(extra)
    return d


def write(project, data):
    (project / "config" / "accounts.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
    )


def test_accounts_load_and_initial_capital(project):
    write(project, {"accounts": [acc("p", "paper", "auto", initial_capital="10000000")]})
    a = S.load_accounts(secrets=Secrets())
    assert a[0].initial_capital == Decimal("10000000") and a[0].acnt_prdt_cd == "01"
    assert a[0].markets == ["KR", "US"]


def test_markets_default_both(project):
    write(project, {"accounts": [{"id": "p", "env": "paper", "cano": "1", "mode": "auto"}]})
    assert S.load_accounts(secrets=Secrets())[0].markets == ["KR", "US"]


def test_accounts_unique_ids(project):
    write(project, {"accounts": [acc("a", "paper", "auto"), acc("a", "paper", "auto")]})
    with pytest.raises(ConfigError, match="중복"):
        S.load_accounts(secrets=Secrets())


def test_real_account_auto_in_yaml_rejected(project):
    write(project, {"accounts": [acc("r", "real", "auto")]})
    with pytest.raises(ConfigError, match="실전"):
        S.load_accounts(secrets=Secrets())


def test_accounts_missing_file_is_empty(project):
    assert S.load_accounts(secrets=Secrets()) == []


def test_unknown_key_is_config_error(project):
    write(project, {"accounts": [acc("p", "paper", "auto", foo=1)]})
    with pytest.raises(ConfigError, match="foo"):
        S.load_accounts(secrets=Secrets())


def test_fo_requires_kr_only(project):
    write(project, {"accounts": [acc("f", "paper", "auto", asset="fo", acnt_prdt_cd="03")]})
    with pytest.raises(ConfigError, match="KR"):
        S.load_accounts(secrets=Secrets())


def test_bad_credentials_prefix_rejected():
    with pytest.raises(ValueError):
        S.Account(id="x", env="real", cano="0", mode="off", credentials="kis_lower_")


def test_accounts_cano_placeholder_filled_from_secrets(project):
    (project / "config" / "accounts.yaml").write_text(
        'accounts:\n  - {id: p, env: paper, cano: "00000000", markets: [KR], mode: auto}\n'
        '  - {id: r, env: real, cano: "12345678", markets: [KR], mode: "off"}\n'
        '  - {id: r2, env: real, cano: "00000000", markets: [KR], mode: "off"}\n',
        encoding="utf-8",
    )
    accts = {a.id: a for a in S.load_accounts(secrets=Secrets(KIS_PAPER_CANO="11112222"))}
    assert (
        accts["p"].cano == "11112222"
        and accts["r"].cano == "12345678"
        and accts["r2"].cano == "00000000"
    )


def test_placeholder_cano_filled_from_account_prefix(project):
    (project / "config" / "accounts.yaml").write_text(
        'accounts:\n  - {id: real-main, env: real, cano: "00000000", mode: "off"}\n'
        '  - {id: real-2, env: real, cano: "00000000", mode: "off"}\n',
        encoding="utf-8",
    )
    sec = Secrets.model_validate(
        {
            "KIS_REAL_APP_KEY": "k",
            "KIS_REAL_APP_SECRET": "s",
            "KIS_REAL_CANO": "11111111",
            "KIS_REAL_2_APP_KEY": "k2",
            "KIS_REAL_2_APP_SECRET": "s2",
            "KIS_REAL_2_CANO": "22222222",
        }
    )
    assert [a.cano for a in S.load_accounts(secrets=sec)] == ["11111111", "22222222"]
    no_cano = Secrets.model_validate({**sec.model_dump(), "KIS_REAL_2_CANO": None})
    assert S.load_accounts(secrets=no_cano)[1].cano == "00000000"


def test_load_accounts_reads_secrets_file_when_not_given(project):
    (project / "config" / "accounts.yaml").write_text(
        'accounts:\n  - {id: p, env: paper, cano: "00000000", markets: [KR], mode: auto}\n',
        encoding="utf-8",
    )
    (project / "secrets" / ".env").write_text("KIS_PAPER_CANO=99990000\n", encoding="utf-8")
    assert S.load_accounts()[0].cano == "99990000"


def test_split_and_to_broker_account():
    s = S.Account(id="p", env="paper", cano="1", mode="auto")
    f = S.Account(
        id="f", env="paper", cano="1", mode="auto", asset="fo", acnt_prdt_cd="03", markets=["KR"]
    )
    assert S.split_accounts([s, f]) == ([s], [f])
    b = S.to_broker_account(f)
    assert (b.id, b.env, b.cano, b.acnt_prdt_cd, b.markets, b.asset) == (
        "f",
        "paper",
        "1",
        "03",
        ["KR"],
        "fo",
    )
    assert not hasattr(b, "mode")
