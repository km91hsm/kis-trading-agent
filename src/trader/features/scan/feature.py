"""scan 기능 선언 (서비스)."""

from trader.features.scan.cli import scan_app
from trader.features.scan.jobs import THEME, events_post_ingest
from trader.features.scan.migrations import MIGRATIONS
from trader.features.scan.ports import PORT
from trader.features.scan.session_kinds import AGENTS, DISCOVER, SCREEN
from trader.features.scan.settings import EVENT_SECTIONS, SCHEDULE_SECTIONS, SECTIONS
from trader.features.scan.tool_cli import TOOLS
from trader.kernel.registry.types import Feature, Hook

FEATURE = Feature(
    id="scan",
    tier="service",
    sections=(*SECTIONS, *SCHEDULE_SECTIONS, *EVENT_SECTIONS),
    hooks=(Hook("news.post_ingest", events_post_ingest),),
    migrations=MIGRATIONS,
    jobs=(THEME,),
    cli=scan_app,
    ports=(PORT,),
    tools=TOOLS,
    agents=AGENTS,
    session_kinds=(SCREEN, DISCOVER),
)
