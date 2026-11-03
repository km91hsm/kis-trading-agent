"""세션 공용 출력 스키마 — 애널리스트 리포트·토론 턴·호출 기록(여러 기능의 세션이 함께 쓴다)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.session.agents import json_schema
from trader.kernel.session.stages import CHECKPOINT_SCHEMA_VERSION

Signal = Literal["STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL", "UNKNOWN"]
Horizon = Literal["short", "mid", "long"]
AnalystRole = Literal["technical", "fundamental", "news_macro", "sentiment"]
Side = Literal["bull", "bear"]
Stance = Literal["BULLISH", "NEUTRAL", "BEARISH"]
ValuationView = Literal["UNDERVALUED", "FAIR", "OVERVALUED", "UNKNOWN"]
TimingAction = Literal["NOW", "CONDITIONAL", "WAIT"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    source_id: str


class AnalystReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    asof: str
    role: AnalystRole
    summary: str = Field(max_length=300)
    bullets: list[str] = Field(max_length=7)
    signal: Signal
    score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    horizon: Horizon
    key_risks: list[str]
    counter_evidence: list[str]
    evidence: list[Evidence]
    data_gaps: list[str]


class Timing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: TimingAction
    condition: str | None = None
    price_zone: str | None = None


class StanceChange(BaseModel):
    """직전 라운드에서 입장(stance)을 바꿀 때 바꾼 이유와 근거(2-L21). evidence 는 애널리스트
    리포트 `evidence[].source_id` 중에서 — 파이썬이 실제로 있는지 확인한다."""

    model_config = ConfigDict(extra="forbid")

    from_stance: Stance
    reason: str = Field(max_length=300)
    evidence: list[str] = Field(max_length=5)


class DebateTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: Side
    round: int = Field(ge=0)
    stance: Stance
    valuation_view: ValuationView
    arguments: list[str] = Field(max_length=5)
    rebuttals: list[str] = Field(max_length=5)
    timing: Timing
    conviction: float = Field(ge=0, le=1)
    what_would_change_my_mind: list[str]
    stance_change: StanceChange | None = None


class CallMeta(BaseModel):
    """LLM 호출 기록 (체크포인트 `call` 필드)."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    model: str | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None


class QuantReport(BaseModel):
    """정량(ML) 모델 보고. 4개 분석가 역할(`AnalystRole`)과 별개 — 모델이 낸 확률과 신호."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    asof: str
    role: Literal["quant"] = "quant"
    model: str
    p_up: float = Field(ge=0, le=1)
    p_flat: float = Field(ge=0, le=1)
    p_down: float = Field(ge=0, le=1)
    signal: Signal
    horizon_days: int = Field(ge=1)
    label_mode: Literal["absolute", "relative_tercile"] = "absolute"
    features_used: list[str]
    data_gaps: list[str] = Field(default_factory=list)
    feature_date: str | None = None


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "AnalystReport",
    "AnalystRole",
    "CallMeta",
    "DebateTurn",
    "Evidence",
    "Horizon",
    "QuantReport",
    "Side",
    "Signal",
    "Stance",
    "StanceChange",
    "Timing",
    "TimingAction",
    "ValuationView",
    "json_schema",
]
