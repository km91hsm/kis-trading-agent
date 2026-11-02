"""ingest 의 sources 섹션 7키."""

import shutil

import pytest
from pydantic import ValidationError

from tests.features.ingest.helpers import write_yaml
from trader.features.ingest.settings import (
    DEFAULT_MARKET_QUERIES,
    NewsCfg,
    SourcesConfig,
    load_sources,
)
from trader.kernel.platform.bundle import templates_dir


def test_sources_template_loads_and_matches_defaults(project):
    shutil.copy(templates_dir() / "config" / "sources.yaml", project / "config" / "sources.yaml")
    cfg = load_sources()
    assert cfg == SourcesConfig()
    assert cfg.channels.naver_blog == []


def test_load_sources_ignores_other_features_keys(project):
    write_yaml(
        project, "sources.yaml", {"saveticker": {"collect": False}, "dart": {"enabled": False}}
    )
    cfg = load_sources()
    assert cfg.dart.enabled is False


def test_edgar_user_agent_optional():
    assert SourcesConfig.model_validate({}).edgar.user_agent == ""


def test_news_cfg_rejects_old_saveticker_keys():
    with pytest.raises(ValidationError):
        NewsCfg(saveticker_dir="x")


def test_market_queries_default_counts():
    assert len(DEFAULT_MARKET_QUERIES["KR"]) == 7
    assert len(DEFAULT_MARKET_QUERIES["US"]) == 5
    assert len(NewsCfg().market_queries["KR"]) == 7
