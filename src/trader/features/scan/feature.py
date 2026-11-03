"""scan 기능 선언 (서비스)."""

from trader.features.scan.jobs import events_post_ingest
from trader.features.scan.migrations import MIGRATIONS
from trader.features.scan.settings import EVENT_SECTIONS, SCHEDULE_SECTIONS, SECTIONS
from trader.kernel.registry.types import Feature, Hook

FEATURE = Feature(
    id="scan",
    tier="service",
    sections=(*SECTIONS, *SCHEDULE_SECTIONS, *EVENT_SECTIONS),
    hooks=(Hook("news.post_ingest", events_post_ingest),),
    migrations=MIGRATIONS,
)
