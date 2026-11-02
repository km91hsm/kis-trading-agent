import pytest
from pydantic import BaseModel, ValidationError

from trader.kernel.config.errors import ConfigError
from trader.kernel.config.sections import Section, SectionRegistry


class A(BaseModel):
    x: int = 0


class B(BaseModel):
    y: int = 0


def test_register_duplicate_key_raises():
    reg = SectionRegistry()
    reg.register(Section("settings", "a", A, A))
    with pytest.raises(ConfigError, match="a"):
        reg.register(Section("settings", "a", A, A))


def test_same_key_in_other_file_is_allowed():
    reg = SectionRegistry()
    reg.register(Section("settings", "a", A, A))
    reg.register(Section("ml", "a", A, A))
    assert reg.keys("settings") == ["a"] and reg.keys("ml") == ["a"]


def test_keys_keeps_registration_order_per_file():
    reg = SectionRegistry()
    reg.register(Section("settings", "b", B, B))
    reg.register(Section("settings", "a", A, A))
    assert reg.keys("settings") == ["b", "a"]
    assert reg.keys("schedule") == []


def test_model_for_forbids_extra_and_uses_defaults():
    reg = SectionRegistry()
    reg.register(Section("settings", "a", A, A))
    reg.register(Section("settings", "b", B))  # default 없음 → 필수
    model = reg.model_for("settings")
    assert model.model_validate({"b": {"y": 2}}).a.x == 0
    with pytest.raises(ValidationError):
        model.model_validate({"b": {}, "zzz": 1})
    with pytest.raises(ValidationError):
        model.model_validate({})


def test_copy_is_independent():
    reg = SectionRegistry()
    reg.register(Section("settings", "a", A, A))
    clone = reg.copy()
    clone.register(Section("settings", "b", B, B))
    assert reg.keys("settings") == ["a"]
    assert clone.keys("settings") == ["a", "b"]
    assert clone.model_for("settings") is not reg.model_for("settings")


def test_kernel_llm_section_defaults_and_timeout_for():
    from trader.kernel.config import UsageProbe, UsageThrottle, kernel_registry

    cfg = kernel_registry().model_for("settings")()
    s = cfg.llm
    assert s.budget_usd == 5.0 and s.timeout_s == 900 and s.daily_session_cap == 80
    assert s.timeout_for("screen") == 300 and s.timeout_for("zzz") == 900
    assert s.usage_throttle == UsageThrottle() and s.usage_probe == UsageProbe()
    assert s.market_session_cap == 2 and s.timeout_retry_max == 2
    assert s.usage_file == "~/.claude/rate_limits.json"


def test_kernel_models_claude_bin_anonymize_sections():
    from trader.kernel.config import kernel_registry

    cfg = kernel_registry().model_for("settings")()
    assert cfg.models == {} and cfg.claude_bin is None and cfg.anonymize is False
