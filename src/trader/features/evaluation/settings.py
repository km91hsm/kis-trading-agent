"""evaluation 설정 섹션: settings.yaml 의 `memory`, schedule.yaml 의 `reflect`·`daily_report`·
`paper_review`·`rl_train`.

yaml 키를 더하면 `templates/config/*.yaml` 도 같이 바꾼다."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.config.sections import Section

_HM = r"^([01]\d|2[0-3]):[0-5]\d$"


class HalfLives(BaseModel):
    model_config = ConfigDict(extra="forbid")

    short: int = 14
    mid: int = 90
    long: int = 365


class MemorySettings(BaseModel):
    """메모리 검색 설정 (스펙 §6.5)."""

    model_config = ConfigDict(extra="forbid")

    half_lives: HalfLives = Field(default_factory=HalfLives)
    warmup: int = 50
    drawdown_stop_pct: float = -0.10
    vix_spike_pct: float = 0.30
    top_k: int = 5


class MarketAt(BaseModel):
    """시각 "HH:MM" 과 시간대(기본 KST)."""

    model_config = ConfigDict(extra="forbid")

    time: str = Field(pattern=_HM)
    tz: str | None = None


class DailyReportSchedule(BaseModel):
    """schedule.yaml `daily_report`: 시장 현지 마감 뒤 시각(평일)."""

    model_config = ConfigDict(extra="forbid")

    kr: str | MarketAt = "16:20"
    us: str | MarketAt = MarketAt(time="16:20", tz="America/New_York")


class WeeklyAt(BaseModel):
    """주간 잡 시각: "HH:MM", APScheduler 요일 표기, 시간대(기본 KST)."""

    model_config = ConfigDict(extra="forbid")

    time: str = Field(pattern=_HM)
    days: str
    tz: str | None = None


SECTIONS = (Section("settings", "memory", MemorySettings, MemorySettings),)
SCHEDULE_SECTIONS = (
    Section("schedule", "reflect", str, lambda: "16:30"),
    Section("schedule", "daily_report", DailyReportSchedule, DailyReportSchedule),
    Section("schedule", "paper_review", WeeklyAt, lambda: WeeklyAt(time="09:00", days="sat")),
    Section("schedule", "rl_train", WeeklyAt, lambda: WeeklyAt(time="10:30", days="sat")),
)
