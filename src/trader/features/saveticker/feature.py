"""saveticker 기능 선언."""

from trader.features.saveticker.cli import app
from trader.features.saveticker.job import JOB
from trader.features.saveticker.settings import SECTION
from trader.kernel.registry.types import Feature

FEATURE = Feature(id="saveticker", tier="service", sections=(SECTION,), jobs=(JOB,), cli=app)
