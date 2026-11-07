"""입력 Section의 직렬화 형식과 의도적인 수집 생략 사유."""

from __future__ import annotations

from typing import Any, TypedDict

# 의도적으로 건너뛴 절의 error 값. 00_inputs.errors 에 집계하지 않는다.
SKIP_REASONS = frozenset(
    {
        "disabled",
        "phase6",
        "phase7",
        "phase8",
        "no saveticker dir",
        "not available for US",
        "no manual input",
        "no sector peers",
    }
)


class Section(TypedDict):
    data: Any
    received_at: str
    error: str | None
