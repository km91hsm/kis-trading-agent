"""saveticker 기능 선언: 설정 절·감독 잡·CLI·웹 `/system/saveticker`(`routes.py`)·nav·제어
네임스페이스 `saveticker_restart_requested`(웹 요청 → 데몬 잡 소비)."""

from trader.features.saveticker.cli import app
from trader.features.saveticker.health import AREA as HEALTH
from trader.features.saveticker.job import JOB
from trader.features.saveticker.restart import RESTART_FLAG
from trader.features.saveticker.routes import ROUTERS
from trader.features.saveticker.settings import SECTION
from trader.kernel.registry.types import Feature, NavItem

FEATURE = Feature(
    id="saveticker", tier="service", sections=(SECTION,), jobs=(JOB,), cli=app, routes=ROUTERS,
    control_namespaces=(RESTART_FLAG,), nav=(NavItem("system", "saveticker", "데이터 수집", 40),),
    health_areas=(HEALTH,),
)  # fmt: skip
