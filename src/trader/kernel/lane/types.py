"""레인 계약 타입. 커널에는 Protocol·DTO 만 둔다(스펙 §2.2)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

# 레인 기능: 어떤 기능도 import 하지 못한다(필요한 것은 deps 주입).
LANES = frozenset({"spot", "fo", "scalp"})


@dataclass(frozen=True)
class WorkItem:
    """collect 가 돌려주는 처리 단위(종목 | 시장 | 후보)."""

    ticker: str
    exchange: str | None


@dataclass
class LaneContext:
    """레인 공통 실행 문맥. 레인 고유 값은 ext 에 둔다."""

    settings: Any
    conn: Any
    state: Path
    now: Callable[[], datetime]
    dry_run: bool
    notifier: Any
    ports: Mapping[str, Any]
    ext: dict = field(default_factory=dict)


class Lane(Protocol):
    """파이프라인 단계의 자리를 고정하고 구현을 플러그한다. DecisionSource 등은 레인 정의 타입."""

    id: str  # spot | fo | scalp
    asset: str  # stock | fo (계좌 필터)

    def schedule(self, schedule: Any, settings: Any) -> list[Any]: ...

    def allowed(self, ctx: LaneContext, market: str, today: date) -> str | None: ...

    def collect(
        self, ctx: LaneContext, market: str, today: date, asof: datetime
    ) -> list[WorkItem]: ...

    def decide(self, ctx: LaneContext, item: WorkItem) -> Any: ...

    def plan(self, ctx: LaneContext, decision: Any, account: Any, fresh: Any) -> Any: ...

    def tick(self, ctx: LaneContext, market: str, today: date) -> Any: ...

    def reevaluate(self, ctx: LaneContext, trigger: Any) -> Any | None: ...

    def reflect(self, ctx: LaneContext, today: date) -> Any | None: ...
