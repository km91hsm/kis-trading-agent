"""spot 기능 선언 (레인). 세션·도구·잡·주문 출처 등은 뒤 Task 가 채운다."""

from trader.features.spot.migrations import MIGRATIONS
from trader.features.spot.settings import SECTIONS
from trader.features.spot.tool_cli import TOOLS
from trader.kernel.registry.types import Feature

FEATURE = Feature(id="spot", tier="lane", sections=SECTIONS, migrations=MIGRATIONS, tools=TOOLS)
