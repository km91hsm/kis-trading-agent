"""ml 기능 선언 (서비스). 잡·CLI·포트는 뒤 Task 에서 더한다."""

from trader.features.ml.migrations import MIGRATIONS
from trader.features.ml.settings import SCHEDULE_SECTIONS, SECTIONS
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="ml",
    tier="service",
    sections=(*SECTIONS, *SCHEDULE_SECTIONS),
    migrations=MIGRATIONS,
)
