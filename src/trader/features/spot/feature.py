"""spot 기능 선언 (레인)."""

from trader.features.spot.cli import spot_app
from trader.features.spot.executor import ORIGINS
from trader.features.spot.jobs import APPROVALS_EXPIRE
from trader.features.spot.migrations import MIGRATIONS
from trader.features.spot.session_kinds import AGENTS, ANALYZE
from trader.features.spot.settings import SECTIONS
from trader.features.spot.telegram import TELEGRAM_COMMANDS
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
    telegram_commands=TELEGRAM_COMMANDS,
    jobs=(APPROVALS_EXPIRE,),
)
