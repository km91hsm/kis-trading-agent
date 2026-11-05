"""ml 기능 선언 (서비스): 퀀트 학습·시점 유니버스 잡, 피처/학습 큐 kind, 기동 백필 훅, ml 포트,
`features` SSE 키."""

from trader.features.ml.cli import ml_app
from trader.features.ml.jobs import (
    FEATURES_SNAPSHOT,
    ML_TRAIN,
    QUEUE_KINDS,
    UNIVERSE_MEMBERS,
    bootstrap,
)
from trader.features.ml.migrations import MIGRATIONS
from trader.features.ml.ports import PORT
from trader.features.ml.routes import ROUTERS, SSE_KEYS
from trader.features.ml.settings import SCHEDULE_SECTIONS, SECTIONS
from trader.features.ml.status import STATUS_SECTION
from trader.kernel.registry.types import Feature, Hook, NavItem

FEATURE = Feature(
    id="ml",
    tier="service",
    sections=(*SECTIONS, *SCHEDULE_SECTIONS),
    migrations=MIGRATIONS,
    cli=ml_app,
    jobs=(ML_TRAIN, UNIVERSE_MEMBERS, FEATURES_SNAPSHOT),
    queue_kinds=QUEUE_KINDS,
    hooks=(Hook("daemon.bootstrap", bootstrap),),
    ports=(PORT,),
    routes=ROUTERS,
    sse_keys=SSE_KEYS,
    status_sections=(STATUS_SECTION,),
    nav=(NavItem("data", "features", "지표·모델", 30),),
)
