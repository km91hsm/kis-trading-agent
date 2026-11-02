"""spot 기능 선언 (레인). 잡 등은 뒤 Task 가 채운다."""

from trader.features.spot.cli import spot_app
from trader.features.spot.executor import ORIGINS
from trader.features.spot.migrations import MIGRATIONS
from trader.features.spot.session_kinds import AGENTS, ANALYZE
from trader.features.spot.settings import SECTIONS
from trader.features.spot.tool_cli import TOOLS
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="spot",
    tier="lane",
    sections=SECTIONS,
    migrations=MIGRATIONS,
    cli=spot_app,
    tools=TOOLS,
    origins=ORIGINS,
    agents=AGENTS,
    session_kinds=(ANALYZE,),
)
