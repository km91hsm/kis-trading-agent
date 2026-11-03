"""ml 기능 선언 (서비스): 퀀트 학습·시점 유니버스 잡, 피처/학습 큐 kind, 기동 백필 훅, ml 포트."""

from trader.features.ml.cli import ml_app
from trader.features.ml.jobs import ML_TRAIN, QUEUE_KINDS, UNIVERSE_MEMBERS, bootstrap
from trader.features.ml.migrations import MIGRATIONS
from trader.features.ml.ports import PORT
from trader.features.ml.settings import SCHEDULE_SECTIONS, SECTIONS
from trader.kernel.registry.types import Feature, Hook

FEATURE = Feature(
    id="ml",
    tier="service",
    sections=(*SECTIONS, *SCHEDULE_SECTIONS),
    migrations=MIGRATIONS,
    cli=ml_app,
    jobs=(ML_TRAIN, UNIVERSE_MEMBERS),
    queue_kinds=QUEUE_KINDS,
    hooks=(Hook("daemon.bootstrap", bootstrap),),
    ports=(PORT,),
)
