"""운영 기준 시간대. 프로젝트에서 ZoneInfo("Asia/Seoul") 을 만드는 곳은 여기 한 곳뿐이다."""

from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")

MARKET_TZ: dict[Literal["KR", "US"], ZoneInfo] = {"KR": KST, "US": ET}
