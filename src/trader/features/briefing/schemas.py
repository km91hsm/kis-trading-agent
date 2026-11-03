"""briefing 에이전트 출력 스키마."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Briefing(BaseModel):
    """아침 브리핑(briefing, Stock docs/06): 사람이 읽는 5줄. 주문·게이트는 읽지 않는다."""

    model_config = ConfigDict(extra="forbid")

    lines: list[str] = Field(min_length=1, max_length=5)
    uncertain: list[str] = Field(default_factory=list, max_length=3)
    confidence: float = Field(ge=0, le=1)

    @field_validator("lines", "uncertain")
    @classmethod
    def _short(cls, v: list[str]) -> list[str]:
        for s in v:
            if len(s) > 160:
                raise ValueError("한 줄은 160자 이내")
        return v
