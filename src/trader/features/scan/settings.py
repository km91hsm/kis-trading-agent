"""scan 설정 섹션: settings.yaml 의 `theme`, schedule.yaml 의 `theme`(주기), 파일 `events`(사건 표).

yaml 키를 더하면 `templates/config/*.yaml` 도 같이 바꾼다."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from trader.kernel.cli.boot import build_registry
from trader.kernel.config import Market
from trader.kernel.config.loader import memo_load
from trader.kernel.config.sections import Section, SectionRegistry
from trader.kernel.registry import discover


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


# --- events.yaml (사건 수혜, 2026-10-27) ---------------------------------------------------


class EventTicker(BaseModel):
    """사건 수혜 종목 하나. 코드는 표가 출처(LLM 이 지어내지 않게), US 는 KIS 거래소 코드."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    name: str
    role: str = ""
    exchange: Literal["NAS", "NYS", "AMS"] | None = None


class EventDef(BaseModel):
    """사건(또는 구조적 흐름) 하나: 뉴스 키워드가 min_news 건 이상이면 '감지'."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    kind: Literal["shock", "structural"] = "shock"
    min_news: int = Field(default=3, ge=1)
    keywords: list[str] = Field(min_length=1)
    # 제외 패턴(2-L10): 한글 키워드는 부분 문자열이라 `전쟁` 이 무역전쟁·반도체전쟁에 걸린다 —
    # 이 패턴이 든 뉴스는 세지 않는다
    exclude: list[str] = Field(default_factory=list)
    beneficiaries: dict[Market, list[EventTicker]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _us_needs_exchange(self) -> EventDef:
        for t in self.beneficiaries.get("US", []):
            if t.exchange is None:
                raise ValueError(f"{self.id}: US 종목 {t.ticker} 에 exchange 가 필요합니다")
        for t in self.beneficiaries.get("KR", []):
            if not re.fullmatch(r"\d{6}", t.ticker):
                raise ValueError(f"{self.id}: KR 코드는 6자리 숫자: {t.ticker}")
        return self


def _unique_ids(events: list[EventDef]) -> list[EventDef]:
    ids = [e.id for e in events]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"사건 id 중복: {sorted(dup)}")
    return events


EVENT_SECTIONS = (
    Section("events", "enabled", bool, lambda: False),
    Section("events", "lookback_hours", Annotated[int, Field(ge=1, le=168)], lambda: 48),
    Section("events", "max_picks_per_event", Annotated[int, Field(ge=1, le=5)], lambda: 2),
    Section("events", "max_event_picks", Annotated[int, Field(ge=0, le=8)], lambda: 4),
    Section("events", "events", Annotated[list[EventDef], AfterValidator(_unique_ids)], list),
)


def load_events(registry: SectionRegistry | None = None, path: Path | None = None) -> Any:
    """`events` 파일의 5섹션 모델(`enabled`·`lookback_hours`·`max_picks_per_event`·
    `max_event_picks`·`events`). 파일이 없으면 꺼짐(빈 표)."""
    return memo_load("events", registry or build_registry(discover()), path)
