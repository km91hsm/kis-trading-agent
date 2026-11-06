"""scan 기능 선언 (서비스)."""

from trader.features.scan.cli import scan_app
from trader.features.scan.jobs import QUEUE_KINDS, THEME
from trader.features.scan.migrations import MIGRATIONS
from trader.features.scan.ports import PORT
from trader.features.scan.routes import ROUTERS, SSE_KEYS
from trader.features.scan.session_kinds import AGENTS, DISCOVER, SCREEN
from trader.features.scan.settings import EVENT_SECTIONS, SCHEDULE_SECTIONS, SECTIONS
from trader.features.scan.status import STATUS_SECTION
from trader.features.scan.telegram import TELEGRAM_COMMANDS
from trader.features.scan.tool_cli import TOOLS
from trader.features.scan.triggers import post_ingest
from trader.kernel.registry.types import Feature, Hook, NavItem

FEATURE = Feature(
    id="scan",
    tier="service",
    sections=(*SECTIONS, *SCHEDULE_SECTIONS, *EVENT_SECTIONS),
    hooks=(Hook("news.post_ingest", post_ingest),),
    migrations=MIGRATIONS,
    jobs=(THEME,),
    cli=scan_app,
    ports=(PORT,),
    tools=TOOLS,
    agents=AGENTS,
    session_kinds=(SCREEN, DISCOVER),
    queue_kinds=QUEUE_KINDS,
    telegram_commands=TELEGRAM_COMMANDS,
    status_sections=(STATUS_SECTION,),
    routes=ROUTERS,
    sse_keys=SSE_KEYS,
    nav=(NavItem("data", "themes", "테마", 20), NavItem("data", "watchlist", "감시목록", 21)),
)
