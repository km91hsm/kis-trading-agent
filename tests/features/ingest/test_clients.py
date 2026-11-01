import logging

from trader.features.ingest import clients
from trader.features.ingest.inputs import InputDeps
from trader.features.ingest.settings import SourcesConfig
from trader.kernel.config.secrets import Secrets


def _cfg(ua: str = "", enabled: bool = True) -> SourcesConfig:
    return SourcesConfig.model_validate({"edgar": {"user_agent": ua, "enabled": enabled}})


def test_build_edgar_prefers_secret_over_yaml(project):
    c = clients.build_edgar(
        _cfg("yaml yaml@example.com"), Secrets(EDGAR_USER_AGENT="trader real@corp.test")
    )
    assert c is not None
    assert "real@corp.test" in c._headers["User-Agent"]


def test_build_edgar_placeholder_disables_and_warns_once(project, monkeypatch, caplog):
    monkeypatch.setattr(clients, "_edgar_warned", False)
    cfg = _cfg(f"trader x{clients.EDGAR_PLACEHOLDER}")
    with caplog.at_level(logging.WARNING, logger="trader.sources"):
        assert clients.build_edgar(cfg, Secrets()) is None
        assert clients.build_edgar(cfg, Secrets()) is None
    warns = [r for r in caplog.records if "EDGAR User-Agent 미설정" in r.getMessage()]
    assert len(warns) == 1
    assert clients.build_edgar(_cfg("", enabled=False), Secrets()) is None
    assert clients.build_edgar(_cfg(""), Secrets()) is None


def test_build_dart_none_without_key(project):
    assert clients.build_dart(Secrets()) is None
    assert clients.build_dart(Secrets(DART_API_KEY="k")) is not None


def test_build_input_deps_without_market(project):
    deps = clients.build_input_deps(Secrets(), _cfg("a b@corp.test"))
    assert isinstance(deps, InputDeps)
    assert deps.market is None and deps.dart is None and deps.edgar is not None
    assert deps.conn_factory is not None and deps.peer_fn is None
