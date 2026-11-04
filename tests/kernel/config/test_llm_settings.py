"""커널 `LlmSettings` 기본값."""

from trader.kernel.config import LlmSettings


def test_timeout_by_kind_default_includes_market():
    assert LlmSettings().timeout_by_kind["market"] == 600
