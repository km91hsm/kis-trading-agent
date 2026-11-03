"""scalp 기능 선언 (레인): 설정 절·판정자 에이전트·`{market}_scalp` 잡·`scalp` 포트."""

from trader.features.scalp.jobs import SCALP_TICK
from trader.features.scalp.judge import AGENT
from trader.features.scalp.ports import PORT
from trader.features.scalp.settings import SECTIONS
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="scalp", tier="lane", sections=SECTIONS, agents=(AGENT,), jobs=(SCALP_TICK,),
    ports=(PORT,),
)  # fmt: skip
