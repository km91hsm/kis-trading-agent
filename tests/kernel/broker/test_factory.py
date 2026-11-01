import pytest

from trader.kernel.broker.broker import KisBroker
from trader.kernel.broker.creds import KisCreds, key_fingerprint
from trader.kernel.broker.factory import (
    build_broker,
    client_for,
    reset_clients,
    shared_state_dir,
)
from trader.kernel.broker.fo import KisFoBroker
from trader.kernel.broker.models import BrokerAccount

from .conftest import FO_PRODUCT


def test_shared_dir_env_creates_and_routes_token_and_rate_db(
    project, respx_mock, monkeypatch, tmp_path
):
    shared = tmp_path / "shared" / "kis"  # 존재하지 않는 중첩 경로
    monkeypatch.setenv("TRADER_KIS_SHARED_DIR", str(shared))
    reset_clients()
    c = client_for(KisCreds(env="paper", prefix="KIS_PAPER_", app_key="k", app_secret="s"))
    assert shared.is_dir()
    assert c.auth.cache_path.parent == shared
    assert c.limiter.path == shared / "kis_rate.db"
    assert not (project / "state" / "kis_rate.db").exists()
    # SharedRateLimiter 는 첫 사용 때 DB 를 만들므로 한 번 쓴 뒤 존재를 확인한다
    c.limiter.acquire()
    assert (shared / "kis_rate.db").exists()
    # 휴장일 캐시는 공유 디렉터리가 아니라 state/ 아래에 남는다
    from trader.kernel.broker.factory import build_market

    market = build_market(KisCreds(env="real", prefix="KIS_REAL_", app_key="k", app_secret="s"))
    cache_dir = market._cache_dir()
    assert cache_dir == project / "state" / "cache"
    assert shared not in cache_dir.parents


def test_without_env_uses_state_dir(project, respx_mock, monkeypatch):
    monkeypatch.delenv("TRADER_KIS_SHARED_DIR", raising=False)
    reset_clients()
    c = client_for(KisCreds(env="paper", prefix="KIS_PAPER_", app_key="k", app_secret="s"))
    assert c.auth.cache_path == project / "state" / f"kis_token_paper-{key_fingerprint('k')}.json"


def test_same_app_key_shares_client(project, respx_mock):
    reset_clients()
    a = KisCreds(env="paper", prefix="KIS_PAPER_", app_key="k", app_secret="s")
    b = KisCreds(env="paper", prefix="KIS_X_", app_key="k", app_secret="s")
    assert client_for(a) is client_for(b)
    assert client_for(a).limiter is client_for(b).limiter
    other_key = KisCreds(env="paper", prefix="KIS_PAPER_", app_key="k2", app_secret="s")
    assert client_for(other_key) is not client_for(a)
    other_env = KisCreds(env="real", prefix="KIS_REAL_", app_key="k", app_secret="s")
    assert client_for(other_env) is not client_for(a)


def test_missing_key_raises(project, respx_mock):
    with pytest.raises(ValueError):
        client_for(KisCreds(env="paper", prefix="KIS_PAPER_"))


def test_build_broker_dispatches_on_asset(project, respx_mock):
    reset_clients()
    creds = KisCreds(env="paper", prefix="KIS_PAPER_", app_key="k", app_secret="s")
    stock = BrokerAccount(id="p", env="paper", cano="00000000")
    fo = BrokerAccount(id="pf", env="paper", cano="00000000", asset="fo")
    assert isinstance(build_broker(stock, creds), KisBroker)
    with pytest.raises(ValueError):
        build_broker(fo, creds)
    assert isinstance(build_broker(fo, creds, fo_product=FO_PRODUCT), KisFoBroker)


def test_shared_dir_tilde_expands(project, monkeypatch, tmp_path, respx_mock):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("TRADER_KIS_SHARED_DIR", "~/kis-shared")
    d = shared_state_dir()
    assert d == (tmp_path / "kis-shared").resolve()
    assert d.is_dir()


def test_shared_dir_relative_rejected(project, monkeypatch, respx_mock):
    monkeypatch.setenv("TRADER_KIS_SHARED_DIR", "rel/kis")
    with pytest.raises(ValueError):
        shared_state_dir()
    # 공백뿐이면 미설정으로 본다
    monkeypatch.setenv("TRADER_KIS_SHARED_DIR", "   ")
    assert shared_state_dir() == project / "state"
