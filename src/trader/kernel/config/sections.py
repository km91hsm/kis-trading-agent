"""설정 섹션 레지스트리: 커널은 레지스트리만 알고, 각 기능이 자기 섹션을 등록한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from trader.kernel.config.errors import ConfigError

ConfigFile = Literal["settings", "schedule", "ml", "sources", "events", "universe", "sectors"]


@dataclass(frozen=True)
class Section:
    """yaml 파일(file) 최상위 키(key) 하나와 그 값의 검증 모델.

    model 은 보통 BaseModel 이지만 `list[Market]` 같은 pydantic 이 검증할 수 있는 타입이면 된다.
    default 가 없으면 그 키는 yaml 에 반드시 있어야 한다."""

    file: ConfigFile
    key: str
    model: Any
    default: Callable[[], Any] | None = None


class SectionRegistry:
    def __init__(self) -> None:
        self._sections: dict[tuple[str, str], Section] = {}
        self._models: dict[str, type[BaseModel]] = {}

    def register(self, section: Section) -> None:
        ident = (section.file, section.key)
        if ident in self._sections:
            raise ConfigError(f"{section.file}.yaml: 섹션 {section.key} 가 이미 등록되었습니다")
        self._sections[ident] = section
        self._models.pop(section.file, None)

    def copy(self) -> SectionRegistry:
        clone = SectionRegistry()
        clone._sections = dict(self._sections)
        return clone

    def sections(self, file: ConfigFile) -> list[Section]:
        return [s for (f, _), s in self._sections.items() if f == file]

    def keys(self, file: ConfigFile) -> list[str]:
        return [s.key for s in self.sections(file)]

    def model_for(self, file: ConfigFile) -> type[BaseModel]:
        """등록된 섹션을 필드로 가진 extra="forbid" 모델 (등록이 바뀔 때까지 같은 클래스)."""
        model = self._models.get(file)
        if model is None:
            fields: dict[str, Any] = {
                s.key: (s.model, Field(default_factory=s.default) if s.default else ...)
                for s in self.sections(file)
            }
            model = create_model(
                f"{file.capitalize()}Config", __config__=ConfigDict(extra="forbid"), **fields
            )
            self._models[file] = model
        return model
