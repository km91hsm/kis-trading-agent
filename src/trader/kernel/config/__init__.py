"""설정 로더 + 섹션 레지스트리. 커널 자체 섹션(settings.markets·web)만 여기서 등록한다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.config.errors import ConfigError
from trader.kernel.config.loader import load, memo_load
from trader.kernel.config.secrets import Secrets, account_prefix, load_secrets
from trader.kernel.config.sections import ConfigFile, Section, SectionRegistry
from trader.kernel.config.time import KST

Env = Literal["paper", "real"]

Market = Literal["KR", "US"]


class WebSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bind: str = "127.0.0.1"
    port: int = 8787


class NotifySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    poll_interval_s: int = Field(30, ge=5, le=600)


KERNEL_SECTIONS: tuple[Section, ...] = (
    Section("settings", "markets", list[Market], lambda: ["KR", "US"]),
    Section("settings", "web", WebSettings, WebSettings),
    Section("settings", "notify", NotifySettings, NotifySettings),
)


def kernel_registry() -> SectionRegistry:
    """커널 섹션만 담은 새 레지스트리 (부트가 copy 해 기능 섹션을 더한다)."""
    reg = SectionRegistry()
    for section in KERNEL_SECTIONS:
        reg.register(section)
    return reg


__all__ = [
    "KERNEL_SECTIONS",
    "KST",
    "ConfigError",
    "ConfigFile",
    "Env",
    "Market",
    "NotifySettings",
    "Section",
    "SectionRegistry",
    "Secrets",
    "WebSettings",
    "account_prefix",
    "kernel_registry",
    "load",
    "load_secrets",
    "memo_load",
]
