"""scan 스키마: screener 출력(`Screened`)과 scout 출력(`Discovered`)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Discovered", "DiscoveredCandidate", "Pick", "Screened"]


class Pick(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    reason: str = Field(max_length=200)


class Screened(BaseModel):
    """screener 출력 (스펙 §5.1): 오늘 분석할 종목 1~5개."""

    model_config = ConfigDict(extra="forbid")

    picks: list[Pick] = Field(min_length=1, max_length=5)
    rejected: list[str] = Field(default_factory=list)


class DiscoveredCandidate(BaseModel):
    """scout 가 발굴한 종목 하나 (Phase 19). 코드 확정·거래소·제외는 파이썬이 한다
    (`scan/discovery.resolve`)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=80)
    ticker: str | None = None
    market: Literal["KR", "US"]
    # event: 사건 수혜(입력 event_watch 의 종목만 — 파이썬이 표로 검증, 2026-10-27)
    tier: Literal["universe", "listed", "event"]
    catalyst: str = Field(max_length=200)
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class Discovered(BaseModel):
    """scout 출력: 오늘 분석할 가치가 있는 종목 ≤ 8 + 메모(악재·발굴 없음 이유)."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[DiscoveredCandidate] = Field(default_factory=list, max_length=8)
    notes: list[str] = Field(default_factory=list, max_length=10)
