"""scan 기능 선언 (서비스)."""

from trader.features.scan.migrations import MIGRATIONS
from trader.features.scan.settings import SCHEDULE_SECTIONS, SECTIONS
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="scan",
    tier="service",
    sections=(*SECTIONS, *SCHEDULE_SECTIONS),
    migrations=MIGRATIONS,
)
