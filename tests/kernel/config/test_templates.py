from pathlib import Path

import pytest

from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.platform.bundle import templates_dir
from trader.kernel.registry import discover


@pytest.fixture(autouse=True)
def _repo_home(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADER_HOME", str(Path(__file__).resolve().parents[3]))


@pytest.mark.parametrize(
    "name", ["settings", "schedule", "accounts", "sources", "universe", "sectors", "events", "ml"]
)
def test_every_template_loads_with_all_sections(name):
    load(name, build_registry(discover()), path=templates_dir() / "config" / f"{name}.yaml")


def test_kernel_defaults():
    cfg = load(
        "settings", build_registry(discover()), path=templates_dir() / "config" / "settings.yaml"
    )
    assert cfg.markets == ["KR", "US"]
    assert (cfg.web.bind, cfg.web.port) == ("127.0.0.1", 8787)
    assert cfg.notify.enabled is True


def test_accounts_template_is_placeholder_only():
    text = (templates_dir() / "config" / "accounts.yaml").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert '"00000000"' in code and "env: real" not in code  # 자리표시자만, 실전 계좌 없음
