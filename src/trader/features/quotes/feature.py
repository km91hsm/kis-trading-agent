"""quotes 기능 선언: 실시간 시세 허브·`/quotes*` 자원·환율(서비스 기능)."""

from trader.features.quotes.routes import ROUTERS
from trader.features.quotes.settings import SECTION
from trader.kernel.registry.types import Feature, NavItem

FEATURE = Feature(
    id="quotes",
    tier="service",
    sections=(SECTION,),
    routes=ROUTERS,
    nav=(NavItem("data", "quotes", "시세·차트", 0),),
    control_namespaces=("fx_cache",),
)
