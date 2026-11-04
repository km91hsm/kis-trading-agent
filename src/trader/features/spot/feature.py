"""spot 기능 선언 (레인)."""

from trader.features.spot.cli import spot_app
from trader.features.spot.executor import ORIGINS
from trader.features.spot.jobs import (
    APPROVALS_EXPIRE,
    BALANCE_REFRESH,
    FILLS_SYNC,
    MONITOR,
    NOTICE_LISTENER,
    QUEUE_KINDS,
    REBALANCE,
    RECONCILE,
)
from trader.features.spot.migrations import MIGRATIONS
from trader.features.spot.ports import PORT
from trader.features.spot.scan_analyze import SCAN_ANALYZE
from trader.features.spot.session_kinds import AGENTS, ANALYZE, RESUME
from trader.features.spot.settings import SCHEDULE_SECTIONS, SECTIONS
from trader.features.spot.telegram import TELEGRAM_COMMANDS
from trader.features.spot.tool_cli import TOOLS
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="spot",
    tier="lane",
    sections=(*SECTIONS, *SCHEDULE_SECTIONS),
    migrations=MIGRATIONS,
    cli=spot_app,
    tools=TOOLS,
    origins=ORIGINS,
    agents=AGENTS,
    session_kinds=(ANALYZE, RESUME),
    telegram_commands=TELEGRAM_COMMANDS,
    jobs=(
        APPROVALS_EXPIRE, FILLS_SYNC, BALANCE_REFRESH, RECONCILE, REBALANCE, MONITOR,
        NOTICE_LISTENER, SCAN_ANALYZE,
    ),  # fmt: skip
    queue_kinds=QUEUE_KINDS,
    ports=(PORT,),
)
