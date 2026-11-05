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


def test_schedule_template_has_ops_check():
    """`ops_check` 내장 잡 주기는 schedule.yaml 이 정한다(템플릿에 키가 있어야 한다)."""
    from trader.kernel.ops.settings import OpsCheckSchedule

    path = templates_dir() / "config" / "schedule.yaml"
    assert "ops_check:" in path.read_text(encoding="utf-8")
    cfg = load("schedule", build_registry(discover()), path=path)
    assert isinstance(cfg.ops_check, OpsCheckSchedule) and cfg.ops_check.interval_s == 1800


def test_templates_have_ops_retention():
    """`retention` 잡 시각·보존 기간은 템플릿 yaml 이 정한다."""
    reg = build_registry(discover())
    root = templates_dir() / "config"
    settings = load("settings", reg, path=root / "settings.yaml")
    schedule = load("schedule", reg, path=root / "schedule.yaml")
    assert settings.ops.retention_days == 120
    assert schedule.retention == "17:20"


def test_env_example_lists_all_secret_fields():
    """`.env.example` 키 = `Secrets` 필드 전부, 값은 모두 비어 있다(자리표시자만)."""
    from trader.kernel.config.secrets import Secrets

    text = (templates_dir() / "secrets" / ".env.example").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    pairs = [ln.split("=", 1) for ln in lines]
    assert {k for k, _ in pairs} == set(Secrets.model_fields)
    assert len(pairs) == len(Secrets.model_fields)
    assert all(v == "" for _, v in pairs)
