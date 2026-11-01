"""ingest 기능 선언 (골격 — 잡·CLI 는 이후 단계에서 더한다)."""

from trader.features.ingest.migrations import MIGRATIONS
from trader.features.ingest.settings import SCHEDULE_SECTION, SECTIONS
from trader.features.ingest.universe import SECTOR_SECTIONS, UNIVERSE_SECTIONS
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="ingest",
    tier="service",
    sections=(*SECTIONS, SCHEDULE_SECTION, *UNIVERSE_SECTIONS, *SECTOR_SECTIONS),
    migrations=MIGRATIONS,
)
