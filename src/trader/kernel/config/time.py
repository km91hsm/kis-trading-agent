"""운영 기준 시간대. 프로젝트에서 ZoneInfo("Asia/Seoul") 을 만드는 곳은 여기 한 곳뿐이다."""

from __future__ import annotations

from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
