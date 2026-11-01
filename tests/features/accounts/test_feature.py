from trader.features.accounts import api
from trader.features.accounts.feature import FEATURE
from trader.kernel.registry import check, discover


def test_feature_declaration():
    assert FEATURE.id == "accounts" and FEATURE.tier == "service"
    assert [s.key for s in FEATURE.sections] == ["accounts"]
    assert [m.version for m in FEATURE.migrations] == [2]
    assert FEATURE.migrations[0].owner == "accounts"
    assert {c.name for c in FEATURE.telegram_commands} == {"pause", "resume", "mode"}
    assert all(not c.untrusted_ok for c in FEATURE.telegram_commands)
    assert [s.title for s in FEATURE.status_sections] == ["계좌"]
    assert FEATURE.control_namespaces == ("paused",)
    assert FEATURE.cli is not None and FEATURE.routes == ()  # 웹 자원은 M11


def test_discovered_and_contracts_pass():
    feats = discover()
    assert "accounts" in [f.id for f in feats] and check(feats) == []


def test_api_surface():
    for name in (
        "load_accounts",
        "split_accounts",
        "to_broker_account",
        "Account",
        "Mode",
        "account_mode",
        "effective_mode",
        "get_account_mode",
        "is_paused",
        "LIVE_CONFIRM",
        "ConfirmationRequired",
        "market_creds",
        "build_brokers",
        "build_market",
    ):
        assert name in api.__all__ and hasattr(api, name)
