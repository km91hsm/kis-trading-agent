"""evaluation 기능 선언 (서비스): 설정 절·원장 v13·제어 네임스페이스. 잡·CLI 는 뒤 Task."""

from trader.features.evaluation.migrations import MIGRATIONS
from trader.features.evaluation.settings import SCHEDULE_SECTIONS, SECTIONS
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="evaluation",
    tier="service",
    sections=(*SECTIONS, *SCHEDULE_SECTIONS),
    migrations=MIGRATIONS,
    control_namespaces=("memory_breaker", "vix_last"),
)
