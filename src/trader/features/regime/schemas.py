"""시장 세션 출력 스키마: 전략가(strategist)의 시장 스탠스·헤지 비중 **제안**.

애널리스트 리포트·토론 턴은 커널 공용(`kernel.session.schemas`)을 그대로 쓴다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.session.schemas import AnalystReport, DebateTurn

__all__ = ["AnalystReport", "DebateTurn", "HedgeProposal", "MarketStance"]

MarketStance = Literal["RISK_ON", "NEUTRAL", "RISK_OFF"]


class HedgeProposal(BaseModel):
    """strategist 출력 (Phase 18 시장 세션): 시장 방향 판단과 헤지 비중 **제안**.

    파이썬 규칙이 최종 결정한다 — 국면 지표가 bear 가 아니면 진입하지 않고, bear 면 비중을
    [hedge.min_weight_pct, hedge.max_weight_pct] 로 자른다.
    레버리지는 거부(leveraged=false)만 가능."""

    model_config = ConfigDict(extra="forbid")

    market: Literal["KR", "US"]
    stance: MarketStance
    hedge_weight: float = Field(ge=0, le=0.25)
    leveraged: bool = False
    scalp_ok: bool = True  # Phase 23: false(확신 ≥ 기준)면 그날 분 단위 단타 금지
    confidence: float = Field(ge=0, le=1)
    horizon_days: int = Field(ge=1)
    rationale: str = Field(max_length=500)
    invalidation_conditions: list[str]
