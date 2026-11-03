"""evaluation 스키마: 복기(Reflection)·보정 리포트(CalibrationReport). 전부 `extra="forbid"`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReviewVerdict = Literal["HIT", "MISS", "UNKNOWN"]
DecisionVerdict = Literal["GOOD", "BAD", "NEUTRAL"]


class SignalReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: ReviewVerdict
    note: str = Field("", max_length=300)


class DecisionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: DecisionVerdict
    note: str = Field("", max_length=500)


class Reflection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: int
    signal_review: dict[str, SignalReview] = {}
    decision_review: DecisionReview
    lesson: str | None = Field(None, max_length=300)
    confidence: float = Field(ge=0, le=1)


class RoleCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hit_rate: float | None
    n: int
    neighbor_hit_rate: float | None
    neighbor_n: int
    bias: float


class DecisionCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: int
    hit_rate: float | None
    avg_direction_score: float
    avg_avoided_loss: float | None


class CalibrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asof: str
    n_decisions: int
    active: bool
    min_decisions: int
    roles: dict[str, RoleCalibration]
    decisions: DecisionCalibration | None
