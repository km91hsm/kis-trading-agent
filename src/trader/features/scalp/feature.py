"""scalp 기능 선언 (레인): 설정 절·판정자 에이전트·`{market}_scalp` 잡·`scalp` 포트·CLI·상태 절·
웹 positions 조각·`/system/scalp`·SSE `scalp`·nav."""

from trader.features.scalp.cli import scalp_app
from trader.features.scalp.jobs import SCALP_TICK
from trader.features.scalp.judge import AGENT
from trader.features.scalp.ports import PORT
from trader.features.scalp.routes import RESOURCES, ROUTERS, SSE_KEYS
from trader.features.scalp.settings import SECTIONS
from trader.features.scalp.status import STATUS_SECTION
from trader.features.scalp.telegram import SCALP_CMD
from trader.kernel.registry.types import Feature, NavItem

FEATURE = Feature(
    id="scalp", tier="lane", sections=SECTIONS, agents=(AGENT,), jobs=(SCALP_TICK,),
    ports=(PORT,), cli=scalp_app, status_sections=(STATUS_SECTION,), resources=RESOURCES,
    routes=ROUTERS, sse_keys=SSE_KEYS, telegram_commands=(SCALP_CMD,),
    nav=(NavItem("today", "scalp-lane", "단타", 20), NavItem("system", "scalp", "단타", 50)),
)  # fmt: skip
