"""픽스처 기능 beta."""

from tests.fixtures.features_ok.alpha.api import hello  # noqa: F401
from trader.kernel.registry.types import Feature, JobSpec, SseKey

FEATURE = Feature(
    id="beta",
    tier="service",
    jobs=(
        JobSpec(
            id="beta.tick",
            kind="beta_tick",
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
    sse_keys=(SseKey(key="beta", probe=lambda ctx: 0),),
)
