import pytest
from pydantic import ValidationError

from trader.kernel.broker.creds import KisCreds, key_fingerprint


def test_bucket_is_env_or_env_fingerprint():
    assert KisCreds(env="paper", prefix="KIS_PAPER_").bucket == "paper"
    c = KisCreds(env="real", prefix="KIS_A_", app_key="k", app_secret="s")
    assert c.bucket == "real-" + key_fingerprint("k") and len(key_fingerprint("k")) == 12
    assert c.shared is False and KisCreds(env="real", prefix="KIS_REAL_").shared is True


def test_broker_account_defaults_and_forbid():
    from trader.kernel.broker.models import BrokerAccount

    a = BrokerAccount(id="paper-main", env="paper", cano="00000000")
    assert (a.markets, a.asset, a.acnt_prdt_cd) == (["KR"], "stock", "01")
    with pytest.raises(ValidationError):
        BrokerAccount(id="x", env="paper", cano="00000000", mode="auto")


def test_token_cache_ignores_token_of_other_app_key(project):
    import json
    from datetime import UTC, datetime, timedelta

    from trader.kernel.broker.auth import KisAuth

    old = KisAuth("real", "old-key", "s", name="real")
    exp = (datetime.now(UTC) + timedelta(hours=10)).isoformat()
    payload = {"access_token": "OLD", "expires_at": exp, "key": key_fingerprint("old-key")}
    old.cache_path.write_text(json.dumps(payload), encoding="utf-8")
    assert old._read_cache().get("token") == "OLD"
    new = KisAuth("real", "new-key", "s", name="real")  # 같은 파일, 키만 바뀜
    assert new._read_cache() == {}
