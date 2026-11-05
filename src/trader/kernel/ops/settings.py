"""운영 점검 설정 모델. `OpsCheckSchedule` 은 schedule.yaml `ops_check`(커널 섹션),
`OpsSettings` 는 settings.yaml `ops`(보존 기간 — `retention` 잡이 쓴다)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OpsCheckSchedule(BaseModel):
    """schedule.yaml 의 ops_check: 데몬 운영 점검 주기(초)."""

    model_config = ConfigDict(extra="forbid")

    interval_s: int = Field(1800, ge=60)


class OpsSettings(BaseModel):
    """settings.yaml 의 ops: 원장 보존 기간(일)."""

    model_config = ConfigDict(extra="forbid")

    retention_days: int = Field(120, ge=30, le=3650)
