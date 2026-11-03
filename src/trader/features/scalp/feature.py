"""scalp 기능 선언 (레인): 설정 절. 에이전트·잡·포트·CLI 는 이후 Task 가 더한다."""

from trader.features.scalp.settings import SECTIONS
from trader.kernel.registry.types import Feature

FEATURE = Feature(id="scalp", tier="lane", sections=SECTIONS)
