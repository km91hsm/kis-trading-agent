"""regime 기능 선언 (서비스). 웹 `/system/regime`(`routes.py`)·nav `system/regime`."""

from trader.features.regime.cli import regime_app
from trader.features.regime.health import AREA as HEALTH
from trader.features.regime.jobs import KSD_EVENTS, QUEUE_KINDS, REGIME
from trader.features.regime.migrations import MIGRATIONS
from trader.features.regime.ports import PORT
from trader.features.regime.routes import ROUTERS
from trader.features.regime.session_kinds import AGENTS, MARKET
from trader.features.regime.settings import KSD_SCHEDULE, REGIME_SCHEDULE, SECTIONS
from trader.features.regime.telegram import KILL, STATUS_SECTION
from trader.features.regime.tool_cli import TOOLS
from trader.kernel.registry.types import Feature, NavItem

FEATURE = Feature(
    id="regime",
    tier="service",
    sections=(*SECTIONS, REGIME_SCHEDULE, KSD_SCHEDULE),
    migrations=MIGRATIONS,
    jobs=(REGIME, KSD_EVENTS),
    queue_kinds=QUEUE_KINDS,
    cli=regime_app,
    telegram_commands=(KILL,),
    status_sections=(STATUS_SECTION,),
    ports=(PORT,),
    tools=TOOLS,
    agents=AGENTS,
    session_kinds=(MARKET,),
    routes=ROUTERS,
    nav=(NavItem("system", "regime", "국면", 30),),
    health_areas=(HEALTH,),
)
