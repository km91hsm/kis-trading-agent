"""saveticker 기능 선언 (Task 6 에서 CLI·잡을 채운다)."""

from trader.features.saveticker.settings import SECTION
from trader.kernel.registry.types import Feature

FEATURE = Feature(id="saveticker", tier="service", sections=(SECTION,))
