"""픽스처 기능 gamma."""

from tests.fixtures.features_bad.alpha.internal import secret  # noqa: F401
from trader.kernel.registry.types import Feature, JobSpec, SseKey

FEATURE = Feature(
    id="gamma",
    tier="service",
    jobs=(
        JobSpec(
            id="gamma.tick",
            kind="gamma_tick",
            trigger="interval",
            per_market=False,
            schedule_key=None,
            interval_s=60,
            enabled=None,
            gate="none",
            heavy=False,
            misfire="default",
            catch_up="none",
            start_soon_s=0,
            body=lambda ctx: None,
        ),
    ),
    sse_keys=(SseKey(key="gamma", probe=lambda ctx: 0),),
)
