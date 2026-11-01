from pathlib import Path

import pytest

from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.platform.bundle import templates_dir
from trader.kernel.registry import discover


@pytest.fixture(autouse=True)
def _repo_home(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADER_HOME", str(Path(__file__).resolve().parents[3]))


@pytest.mark.parametrize("name", ["settings", "schedule"])
def test_every_template_loads_with_all_sections(name):
    load(name, build_registry(discover()), path=templates_dir() / "config" / f"{name}.yaml")


def test_kernel_defaults():
    cfg = load(
        "settings", build_registry(discover()), path=templates_dir() / "config" / "settings.yaml"
    )
    assert cfg.markets == ["KR", "US"]
    assert (cfg.web.bind, cfg.web.port) == ("127.0.0.1", 8787)
