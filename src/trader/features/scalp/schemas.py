"""scalp 에이전트 출력 스키마(판정자·보유 조언)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScalpVerdict(BaseModel):
    """진입 판정자(scalp-judge): 신호 하나에 GO/NO_GO 와 청산 파라미터."""

    model_config = ConfigDict(extra="forbid")

    go: bool
    size: Literal["full", "half"] = "full"
    take_profit_pct: float = Field(gt=0, le=0.05)
    stop_pct: float = Field(gt=0, le=0.03)
    max_hold_min: int = Field(ge=1, le=120)
    reason: str = Field(max_length=300)
    confidence: float = Field(ge=0, le=1)


class ScalpAdvice(BaseModel):
    """보유 중 조언: 유지(hold) 또는 조기 청산(exit)만. 손절·마감은 파이썬이 집행한다."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["hold", "exit"]
    reason: str = Field(max_length=300)
    confidence: float = Field(ge=0, le=1)
