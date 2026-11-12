"""quotes 기능 선언: 실시간 시세 허브·`/quotes*`·`/names*`·`/charts*` 자원·환율·
종목명 채우기 잡 `names_fill`(서비스 기능)."""

from trader.features.quotes import charts, names
from trader.features.quotes.health import AREA as HEALTH
from trader.features.quotes.jobs import MARKET_ACTIVITY, MISS_FLAG, NAMES_FILL, reclaim_ledger
from trader.features.quotes.migrations import MIGRATIONS, VACUUM_FLAG
from trader.features.quotes.ports import PORT
from trader.features.quotes.routes import ROUTERS
from trader.features.quotes.settings import ACTIVITY_SCHEDULE_SECTION, SCHEDULE_SECTION, SECTION
from trader.kernel.registry.types import Feature, Hook, NavItem

FEATURE = Feature(
    id="quotes",
    tier="service",
    migrations=MIGRATIONS,
    sections=(SECTION, SCHEDULE_SECTION, ACTIVITY_SCHEDULE_SECTION),
    ports=(PORT,),
    jobs=(NAMES_FILL, MARKET_ACTIVITY),
    routes=(*ROUTERS, names.router, charts.router),
    nav=(NavItem("data", "quotes", "시세·차트", 0),),
    hooks=(Hook("daemon.bootstrap", reclaim_ledger),),
    control_namespaces=("fx_cache", MISS_FLAG, VACUUM_FLAG),
    health_areas=(HEALTH,),
)
