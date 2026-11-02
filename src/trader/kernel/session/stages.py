"""단계 전제조건 검사: `StageRule` 이 허용하는 단계인지, 선행 단계가 갖춰졌는지."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from fnmatch import fnmatchcase

from trader.kernel.registry.types import StageRule

CHECKPOINT_SCHEMA_VERSION = 1


def first_rule(rules: Iterable[StageRule], stage: str) -> StageRule | None:
    """stage 에 패턴이 맞는 첫 규칙(없으면 None)."""
    return next((r for r in rules if fnmatchcase(stage, r.pattern)), None)


def check_rule(rule: StageRule, stage: str, stages: Iterable[str]) -> list[str]:
    """규칙 위반 사유 목록(빈 리스트면 통과). stages: 이미 기록된 단계 이름들."""
    if not fnmatchcase(stage, rule.pattern):
        return [f"단계 {stage} 는 이 도구가 쓸 수 없음"]
    have: Sequence[str] = list(stages)
    msgs = [f"{name} 없음" for name in rule.requires if name not in have]
    for glob, n in rule.min_counts:
        k = sum(1 for s in have if fnmatchcase(s, glob))
        if k < n:
            msgs.append(f"{glob} {n}개 필요(현재 {k})")
    return msgs
