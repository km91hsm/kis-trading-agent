"""scalp 기능 선언 (레인): 설정 절·판정자 에이전트·`{market}_scalp` 잡·`scalp` 포트·CLI·상태 절."""

from trader.features.scalp.cli import scalp_app
from trader.features.scalp.jobs import SCALP_TICK
from trader.features.scalp.judge import AGENT
from trader.features.scalp.ports import PORT
from trader.features.scalp.settings import SECTIONS
from trader.features.scalp.status import STATUS_SECTION
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="scalp", tier="lane", sections=SECTIONS, agents=(AGENT,), jobs=(SCALP_TICK,),
    ports=(PORT,), cli=scalp_app, status_sections=(STATUS_SECTION,),
)  # fmt: skip
