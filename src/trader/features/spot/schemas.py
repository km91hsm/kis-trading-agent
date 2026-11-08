"""spot 에이전트 출력 스키마와 체크포인트 이름 → 스키마 매핑."""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trader.kernel.session.schemas import (
    CHECKPOINT_SCHEMA_VERSION,
    AnalystReport,
    AnalystRole,
    CallMeta,
    DebateTurn,
    Evidence,
    Horizon,
    Side,
    Signal,
    Stance,
    StanceChange,
    Timing,
    TimingAction,
    ValuationView,
    json_schema,
)

Action = Literal["BUY", "SELL", "HOLD", "ADD", "TRIM"]
OrderType = Literal["LIMIT", "MARKET"]

ANALYST_ROLES = ("technical", "fundamental", "news_macro", "sentiment")

# 체크포인트 이름 규약(파이썬이 만드는 00_·25_·40_·50_ 단계는 스키마 없음)
CHECKPOINT_NAMES = (
    "00_inputs",
    "10_analyst_<role>",
    "20_debate_r<n>_<side>",
    "20_debatex_r<n>_<side>",
    "25_research_summary",
    "30_decision",
    "40_risk_check_<account>",
    "50_order_<account>",
)


class InvalidationRule(BaseModel):
    """장중 모니터가 검사하는 무효화 조건(10-26 검토 L16). `price` 는 현재가(days 1), `close` 는
    완성 일봉 종가가 `days` 거래일 연속 조건을 만족할 때. 문자열 `invalidation_conditions` 는
    설명용이다."""

    model_config = ConfigDict(extra="forbid")

    metric: Literal["price", "close"]
    op: Literal["<", "<=", ">", ">="]
    value: float = Field(gt=0)
    days: int = Field(default=1, ge=1, le=20)

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("value 는 유한한 양수여야 합니다")
        return v

    @field_validator("days")
    @classmethod
    def _days_for_close_only(cls, v: int, info) -> int:
        if v > 1 and info.data.get("metric") == "price":
            raise ValueError("price 조건은 days 1 만 — 연속 일수는 close 로")
        return v


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    action: Action
    target_weight: float = Field(ge=0, le=0.25)
    order_type: OrderType
    # 가격은 양수만(전체 검토 O16): 음수·0 손절가는 기본 −15% 손절까지 무력화한다
    limit_price: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    horizon_days: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=500)
    invalidation_conditions: list[str]
    invalidation_rules: list[InvalidationRule] = Field(default_factory=list, max_length=5)
    memory_ids_used: list[str] = Field(default_factory=list)


_STAGE_PATTERNS: list[tuple[re.Pattern[str], type[BaseModel]]] = [
    (re.compile(r"^10_analyst_[a-z_]+$"), AnalystReport),
    (re.compile(r"^20_debatex?_r\d+_(bull|bear)$"), DebateTurn),
    (re.compile(r"^30_decision$"), Decision),
]


def schema_for(stage: str) -> type[BaseModel] | None:
    """체크포인트 이름에 해당하는 스키마. 파이썬이 만드는 단계(요약 등)는 None."""
    for pattern, model in _STAGE_PATTERNS:
        if pattern.match(stage):
            return model
    return None


__all__ = [
    "ANALYST_ROLES",
    "CHECKPOINT_NAMES",
    "CHECKPOINT_SCHEMA_VERSION",
    "AnalystReport",
    "AnalystRole",
    "CallMeta",
    "Decision",
    "DebateTurn",
    "Evidence",
    "Horizon",
    "InvalidationRule",
    "Side",
    "Signal",
    "Stance",
    "StanceChange",
    "Timing",
    "TimingAction",
    "ValuationView",
    "json_schema",
    "schema_for",
]
