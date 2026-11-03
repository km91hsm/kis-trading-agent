"""regime 기능 선언 (서비스)."""

from trader.features.regime.migrations import MIGRATIONS
from trader.features.regime.ports import PORT
from trader.features.regime.settings import SECTIONS
from trader.kernel.registry.types import Feature

FEATURE = Feature(
    id="regime", tier="service", sections=SECTIONS, migrations=MIGRATIONS, ports=(PORT,)
)
