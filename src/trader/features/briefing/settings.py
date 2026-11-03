"""briefing 설정: settings.yaml 의 `briefing`, schedule.yaml 의 `briefing`(시각 "HH:MM", KST).

yaml 키를 더하면 `templates/config/*.yaml` 도 같이 바꾼다."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.config.sections import Section


class BriefingCfg(BaseModel):
    """아침 브리핑(Stock docs/06): 밤사이 미국장·거시·뉴스·보유 영향을 5줄로(haiku 단발 1회)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    daily_cap: int = Field(default=1, ge=0, le=5)
    timeout_s: int = Field(default=120, ge=10, le=600)


SECTIONS = (Section("settings", "briefing", BriefingCfg, BriefingCfg),)
SCHEDULE_SECTIONS = (Section("schedule", "briefing", str, lambda: "07:20"),)
