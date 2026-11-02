"""ingest 기능 선언."""

from trader.features.ingest.cli import ingest_app
from trader.features.ingest.job import JOB
from trader.features.ingest.migrations import MIGRATIONS
from trader.features.ingest.settings import SCHEDULE_SECTION, SECTIONS
from trader.features.ingest.universe import SECTOR_SECTIONS, UNIVERSE_SECTIONS
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="ingest",
    tier="service",
    sections=(*SECTIONS, SCHEDULE_SECTION, *UNIVERSE_SECTIONS, *SECTOR_SECTIONS),
    migrations=MIGRATIONS,
    jobs=(JOB,),
    cli=ingest_app,
)
