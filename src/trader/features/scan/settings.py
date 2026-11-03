"""scan 설정 섹션: settings.yaml 의 `theme`, schedule.yaml 의 `theme`(주기).

yaml 키를 더하면 `templates/config/*.yaml` 도 같이 바꾼다."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.config.sections import Section


class ThemeWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    news: float = 0.35
    rank: float = 0.25
    flow: float = 0.15
    saveticker: float = 0.10
    vol: float = 0.15


class ThemeCfg(BaseModel):
    """테마 열기 감지 (Phase 25 단계 4, `theme.py`). 감지·알림·scout 입력만 — 매매는 분석 세션.
    주기는 schedule.yaml `theme.interval_s`(`ThemeSchedule`)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    top_n: int = Field(default=3, ge=1, le=10)  # 알림·scout·게이트가 보는 상위 테마 수
    min_cluster_titles: int = Field(default=3, ge=2)  # 키워드 테마: 새 뉴스 제목 공출현 최소 건수
    weights: ThemeWeights = Field(default_factory=ThemeWeights)


class ThemeSchedule(BaseModel):
    """schedule.yaml `theme`: 시장별 테마 잡 주기(장중만)."""

    model_config = ConfigDict(extra="forbid")

    interval_s: int = Field(default=1800, ge=300, le=86400)


SECTIONS = (Section("settings", "theme", ThemeCfg, ThemeCfg),)
SCHEDULE_SECTIONS = (Section("schedule", "theme", ThemeSchedule, ThemeSchedule),)
