"""accounts 기능 선언."""

from trader.features.accounts.settings import SECTION
from trader.kernel.registry.types import Feature

FEATURE = Feature(id="accounts", tier="service", sections=(SECTION,))
