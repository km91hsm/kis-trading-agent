"""briefing 기능 선언 (서비스): 설정 절·아침 브리핑 잡·에이전트."""

from trader.features.briefing.briefing import AGENT
from trader.features.briefing.jobs import MORNING_BRIEFING
from trader.features.briefing.settings import SCHEDULE_SECTIONS, SECTIONS
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="briefing",
    tier="service",
    sections=(*SECTIONS, *SCHEDULE_SECTIONS),
    jobs=(MORNING_BRIEFING,),
    agents=(AGENT,),
)
