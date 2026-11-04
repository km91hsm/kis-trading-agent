"""settings.yaml `quotes` 섹션: 실시간 허브 사용 여부와 REST 폴링 주기."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.config import Section


class QuotesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 국내 정규장 실시간 허브(KIS 웹소켓). 끄면 국내도 REST 폴링만
    realtime_ws: bool = True
    # 미국 정규장 REST 폴링 주기(ms) — 화면이 `next_poll_ms` 로 받는다
    us_poll_ms: int = Field(5000, ge=1000)
    # 파생(선물옵션) 정규장 REST 폴링 주기(ms)
    fo_poll_ms: int = Field(2000, ge=1000)


SECTION = Section("settings", "quotes", QuotesSettings, QuotesSettings)
