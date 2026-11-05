"""운영 기준 시간대. 프로젝트에서 ZoneInfo("Asia/Seoul") 을 만드는 곳은 여기 한 곳뿐이다."""

from __future__ import annotations

import re
from typing import Literal
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")

MARKET_TZ: dict[Literal["KR", "US"], ZoneInfo] = {"KR": KST, "US": ET}

# "HH:MM"(00:00~23:59). 그룹 1·2 가 시·분. pydantic `Field(pattern=...)` 에는 `.pattern` 을 쓴다
HM_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def check_hm(value: str) -> str:
    """시각 문자열이 HH:MM 이면 그대로 돌려주고 아니면 ValueError(끝 개행도 거부 — fullmatch)."""
    if not isinstance(value, str) or not HM_PATTERN.fullmatch(value):
        raise ValueError(f"시각은 HH:MM 형식이어야 합니다: {value!r}")
    return value
