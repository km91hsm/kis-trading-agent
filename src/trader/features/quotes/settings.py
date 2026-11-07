"""settings.yaml `quotes` 섹션: 실시간 허브 사용 여부, REST 폴링 주기, KIS 환율 종목코드,
고정 종목명. schedule.yaml `names_fill`: 종목명 채우기 잡 주기."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trader.kernel.config import Section

_NAME_KEY_RE = re.compile(r"(KR|US):[A-Za-z0-9.\-]+")


class QuotesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 실전 공용 KIS 키가 준비된 데몬에서 정규장 관측 창에만 수집한다.
    activity_enabled: bool = True
    activity_max_pages: int = Field(2, ge=1, le=5)
    activity_limit: int = Field(100, ge=1, le=200)

    # 국내 정규장 실시간 허브(KIS 웹소켓). 끄면 국내도 REST 폴링만
    realtime_ws: bool = True
    # 미국 정규장 REST 폴링 주기(ms) — 화면이 `next_poll_ms` 로 받는다
    us_poll_ms: int = Field(5000, ge=1000)
    # 파생(선물옵션) 정규장 REST 폴링 주기(ms)
    fo_poll_ms: int = Field(2000, ge=1000)
    # KIS 환율 종목코드(FHKST03030100, 시장 X). `FX@KRW`(원/달러 KMB) 또는 `FX@KRWKFTC` — 차이는
    # 실기 미확인(docs/research/2026-11-05-kis-fx-rate-api.md)
    fx_code: str = "FX@KRW"
    # 고정 종목명 `"<시장>:<종목>": 이름` — DART·EDGAR 캐시에 없는 ETF(헤지·현금·지수 대용) 등.
    # 데몬 `names_fill` 이 원장 `ticker_names` 에 source='config' 로 적재한다(관측 이름 우선)
    instrument_names: dict[str, str] = Field(default_factory=dict)

    @field_validator("fx_code")
    @classmethod
    def _fx_code_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("quotes.fx_code 는 비어 있을 수 없습니다(예: FX@KRW)")
        return v

    @field_validator("instrument_names")
    @classmethod
    def _instrument_keys(cls, v: dict[str, str]) -> dict[str, str]:
        out = {}
        for key, name in v.items():
            if not _NAME_KEY_RE.fullmatch(key):
                raise ValueError(
                    f"quotes.instrument_names 키는 '<KR|US>:<종목>' 이어야 합니다: {key!r}"
                )
            if name.strip():
                out[key] = name.strip()
        return out


class NamesFillSchedule(BaseModel):
    """schedule.yaml `names_fill`: 이름 없는 보유·주문 종목 채우기 주기(초)."""

    model_config = ConfigDict(extra="forbid")

    interval_s: int = Field(default=3600, ge=60)


SECTION = Section("settings", "quotes", QuotesSettings, QuotesSettings)
SCHEDULE_SECTION = Section("schedule", "names_fill", NamesFillSchedule, NamesFillSchedule)


class ActivitySchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_s: int = Field(30, ge=30, le=3600)


ACTIVITY_SCHEDULE_SECTION = Section(
    "schedule", "market_activity", ActivitySchedule, ActivitySchedule
)
