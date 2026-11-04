"""fo 기능 선언 (레인): 설정 절·마이그레이션 14·control 네임스페이스.

잡·도구·세션·에이전트·CLI·텔레그램·상태 절은 뒤 Task 가 채운다."""

from trader.features.fo.migrations import MIGRATIONS
from trader.features.fo.settings import SECTIONS
from trader.kernel.registry.types import Feature

# 옛 control 플래그 키 접두(A4): `fo_verified:<env>:<acct>:<product>:<vehicle>` 등
CONTROL_NAMESPACES = (
    "fo_verified", "fo_halted", "fo_day_open", "fo_alerts", "fo_ref_iv", "fo_pending_entry",
    "fo_replace",
)  # fmt: skip

FEATURE = Feature(
    id="fo", tier="lane", sections=SECTIONS, migrations=MIGRATIONS,
    control_namespaces=CONTROL_NAMESPACES,
)  # fmt: skip
