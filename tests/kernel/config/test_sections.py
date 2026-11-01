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
