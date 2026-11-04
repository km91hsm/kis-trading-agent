"""데몬이 올리는 기능 전체: 발견된 기능 + 커널 잡 큐(`QUEUE_FEATURE`)
+ 커널 내장(`BUILTIN_FEATURE`).
데몬 스케줄·큐 kind 표, `trader jobs`, 텔레그램 `/run`, 웹 `/api/jobs` 가 같은 목록을 본다."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def with_kernel(features: Iterable[Any]) -> list[Any]:
    """`[*features, QUEUE_FEATURE, BUILTIN_FEATURE]`. 순환 import 를 피해 부를 때 가져온다."""
    from trader.kernel.daemon.builtin_jobs import BUILTIN_FEATURE
    from trader.kernel.daemon.queue_jobs import QUEUE_FEATURE

    return [*features, QUEUE_FEATURE, BUILTIN_FEATURE]
