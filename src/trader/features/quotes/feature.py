"""quotes 기능 선언: 실시간 시세 허브·`/quotes*`·`/names*`·`/charts*` 자원·환율·
종목명 채우기 잡 `names_fill`(서비스 기능)."""

from trader.features.quotes import charts, names
from trader.features.quotes.health import AREA as HEALTH
from trader.features.quotes.jobs import MISS_FLAG, NAMES_FILL
from trader.features.quotes.routes import ROUTERS
from trader.features.quotes.settings import SCHEDULE_SECTION, SECTION
from trader.kernel.registry.types import Feature, NavItem

FEATURE = Feature(
    id="quotes",
    tier="service",
    sections=(SECTION, SCHEDULE_SECTION),
    jobs=(NAMES_FILL,),
    routes=(*ROUTERS, names.router, charts.router),
    nav=(NavItem("data", "quotes", "시세·차트", 0),),
    control_namespaces=("fx_cache", MISS_FLAG),
    health_areas=(HEALTH,),
)
