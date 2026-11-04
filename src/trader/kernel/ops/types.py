"""운영 점검 공용 타입: 점검 항목 `Item`·영역 문맥 `OpsContext`·커널 예약 영역 key.
러너(`health`)·커널 영역(`areas`)·계약 검사(`registry.contracts`)가 함께 쓴다(순환 없는 잎 모듈)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trader.kernel.registry.types import Feature

# 커널 내장 영역 key(`areas.KERNEL_AREAS`). 기능 영역은 이 key 를 쓸 수 없다
RESERVED_AREA_KEYS: tuple[str, ...] = ("jobs", "llm", "orders")


@dataclass
class Item:
    key: str  # 영역.세부 (예: orders.unknown)
    level: str
    text: str
    link: str | None = None  # 웹 경로 (조치 필요 행이 가리킨다)
    count: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OpsContext:
    """영역 check 함수가 받는 읽기 전용 문맥. ports 는 부트가 조립한 포트(`accounts` 등)."""

    conn: sqlite3.Connection
    settings: Any
    state: Path
    now: datetime
    ports: Mapping[str, Any]
    jobs: Sequence[Any]
    features: Sequence[Feature]
