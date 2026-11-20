"""sources.yaml 의 `saveticker` 섹션."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import memo_load
from trader.kernel.config.sections import Section, SectionRegistry
from trader.kernel.registry import discover


class SaveTickerCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir: str | None = "state/external/saveticker"
    collect: bool = True
    retention_days: int = Field(30, ge=3, le=365)


SECTION = Section("sources", "saveticker", SaveTickerCfg, SaveTickerCfg)


@cache
def _default_registry() -> SectionRegistry:
    return build_registry(discover())


def load_cfg(path: Path | None = None, *, registry: SectionRegistry | None = None) -> SaveTickerCfg:
    """sources.yaml 의 saveticker 섹션. 파일이 없으면 기본값(섹션 default), 잘못된 내용은
    ConfigError."""
    reg = registry or _default_registry()
    return memo_load("sources", reg, path).saveticker  # type: ignore[attr-defined]
