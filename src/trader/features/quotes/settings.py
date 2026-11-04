"""settings.yaml `quotes` 섹션: 실시간 허브 사용 여부, REST 폴링 주기, KIS 환율 종목코드."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trader.kernel.config import Section


class QuotesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 국내 정규장 실시간 허브(KIS 웹소켓). 끄면 국내도 REST 폴링만
    realtime_ws: bool = True
    # 미국 정규장 REST 폴링 주기(ms) — 화면이 `next_poll_ms` 로 받는다
    us_poll_ms: int = Field(5000, ge=1000)
    # 파생(선물옵션) 정규장 REST 폴링 주기(ms)
    fo_poll_ms: int = Field(2000, ge=1000)
    # KIS 환율 종목코드(FHKST03030100, 시장 X). `FX@KRW`(원/달러 KMB) 또는 `FX@KRWKFTC` — 차이는
    # 실기 미확인(docs/research/2026-11-05-kis-fx-rate-api.md)
    fx_code: str = "FX@KRW"

    @field_validator("fx_code")
    @classmethod
    def _fx_code_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("quotes.fx_code 는 비어 있을 수 없습니다(예: FX@KRW)")
        return v


SECTION = Section("settings", "quotes", QuotesSettings, QuotesSettings)
