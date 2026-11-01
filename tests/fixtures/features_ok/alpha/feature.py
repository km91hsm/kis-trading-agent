"""픽스처 기능 alpha."""

from trader.kernel.registry.types import Feature, JobSpec, SseKey

FEATURE = Feature(
    id="alpha",
    tier="service",
    jobs=(
        JobSpec(
            id="alpha.tick",
            kind="alpha_tick",
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
    sse_keys=(SseKey(key="alpha", probe=lambda ctx: 0),),
)
