"""기능이 선언한 Hook 을 포인트별로 묶고, 실패를 격리해 실행한다. 커널은 훅 내용을 모른다."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from trader.kernel.registry.types import Feature, Hook

logger = logging.getLogger("trader.registry")


def by_point(features: Iterable[Feature]) -> dict[str, tuple[Hook, ...]]:
    """포인트 → 훅 튜플. 기능 순서·기능 안 선언 순서를 유지한다."""
    grouped: dict[str, list[Hook]] = {}
    for feature in features:
        for hook in feature.hooks:
            grouped.setdefault(hook.point, []).append(hook)
    return {point: tuple(hs) for point, hs in grouped.items()}


def run(hooks: Mapping[str, tuple[Hook, ...]] | None, point: str, **kw: Any) -> list[dict]:
    """포인트의 훅을 차례로 부른다. 훅 하나의 예외는 기록하고 다음 훅을 계속한다.
    훅마다 {"hook", "result"} 또는 {"hook", "error"} 한 건."""
    if not hooks:
        return []
    out: list[dict] = []
    for hook in hooks.get(point, ()):
        name = f"{hook.fn.__module__}.{hook.fn.__qualname__}"
        try:
            out.append({"hook": name, "result": hook.fn(**kw)})
        except Exception as e:  # noqa: BLE001 - 훅 하나의 실패가 단계를 막지 않는다
            logger.exception("훅 실패 %s (%s)", name, point)
            out.append({"hook": name, "error": f"{type(e).__name__}: {e}"})
    return out
