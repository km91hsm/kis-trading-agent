"""단타 청산: 계좌별 다리·체결 동기화·취소·흡수·청산 단계·이월.

Task 5 시점에는 자리만 둔다 — `engine.tick` 이 부르는 순서(`carry_over` → 열린 포지션이 있으면
`check_exits`)는 고정돼 있고, 본문은 Task 6 이 옛 엔진 §F 를 이식해 채운다."""

from __future__ import annotations

from datetime import date
from typing import Any


def carry_over(ctx: Any, market: str, today: date, st: dict) -> None:
    """지난 운영일의 미종결 단타를 오늘 상태로 옮긴다(Task 6)."""
    return None


def check_exits(
    ctx: Any, *, market: str, today: date, st: dict, skip: frozenset[str], out: dict
) -> None:
    """열린 단타의 체결 동기화·청산 규칙·청산 단계(Task 6)."""
    return None
