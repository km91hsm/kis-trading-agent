import pytest

from trader.kernel.broker.broker import KisBroker
from trader.kernel.broker.creds import KisCreds, key_fingerprint
from trader.kernel.broker.factory import build_broker, client_for, reset_clients
from trader.kernel.broker.fo import KisFoBroker
from trader.kernel.broker.models import BrokerAccount

from .conftest import FO_PRODUCT


def test_shared_dir_env_creates_and_routes_token_and_rate_db(project, monkeypatch, tmp_path):
    shared = tmp_path / "shared" / "kis"  # 존재하지 않는 중첩 경로
    monkeypatch.setenv("TRADER_KIS_SHARED_DIR", str(shared))
    reset_clients()
    c = client_for(KisCreds(env="paper", prefix="KIS_PAPER_", app_key="k", app_secret="s"))
    assert shared.is_dir()
    assert c.auth.cache_path.parent == shared
    assert c.limiter.path == shared / "kis_rate.db"
    assert not (project / "state" / "kis_rate.db").exists()


def test_without_env_uses_state_dir(project, monkeypatch):
    monkeypatch.delenv("TRADER_KIS_SHARED_DIR", raising=False)
    reset_clients()
    c = client_for(KisCreds(env="paper", prefix="KIS_PAPER_", app_key="k", app_secret="s"))
    assert c.auth.cache_path == project / "state" / f"kis_token_paper-{key_fingerprint('k')}.json"


def test_same_app_key_shares_client(project):
    reset_clients()
    a = KisCreds(env="paper", prefix="KIS_PAPER_", app_key="k", app_secret="s")
    b = KisCreds(env="paper", prefix="KIS_X_", app_key="k", app_secret="s")
    assert client_for(a) is client_for(b)


def test_missing_key_raises(project):
    with pytest.raises(ValueError):
        client_for(KisCreds(env="paper", prefix="KIS_PAPER_"))


def test_build_broker_dispatches_on_asset(project):
    reset_clients()
    creds = KisCreds(env="paper", prefix="KIS_PAPER_", app_key="k", app_secret="s")
    stock = BrokerAccount(id="p", env="paper", cano="00000000")
    fo = BrokerAccount(id="pf", env="paper", cano="00000000", asset="fo")
    assert isinstance(build_broker(stock, creds), KisBroker)
    with pytest.raises(ValueError):
        build_broker(fo, creds)
    assert isinstance(build_broker(fo, creds, fo_product=FO_PRODUCT), KisFoBroker)
