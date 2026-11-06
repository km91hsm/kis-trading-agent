"""ingest 기능 선언."""

from trader.features.ingest.cli import ingest_app
from trader.features.ingest.health import AREA as HEALTH
from trader.features.ingest.job import JOB, QUEUE_KINDS
from trader.features.ingest.migrations import MIGRATIONS
from trader.features.ingest.ports import PORT
from trader.features.ingest.routes import ROUTERS, SSE_KEYS
from trader.features.ingest.settings import SCHEDULE_SECTION, SECTIONS
from trader.features.ingest.universe import SECTOR_SECTIONS, UNIVERSE_SECTIONS
from trader.kernel.registry.types import Feature, NavItem

FEATURE = Feature(
    id="ingest",
    tier="service",
    sections=(*SECTIONS, SCHEDULE_SECTION, *UNIVERSE_SECTIONS, *SECTOR_SECTIONS),
    migrations=MIGRATIONS,
    jobs=(JOB,),
    queue_kinds=QUEUE_KINDS,
    cli=ingest_app,
    ports=(PORT,),
    routes=ROUTERS,
    sse_keys=SSE_KEYS,
    nav=(NavItem("data", "news", "뉴스·공시", 10),),
    health_areas=(HEALTH,),
)
