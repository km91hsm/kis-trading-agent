"""evaluation 기능 선언 (서비스): 설정 절·원장 v13·제어 네임스페이스. 잡·CLI 는 뒤 Task."""

from trader.features.evaluation.cli import evaluation_app
from trader.features.evaluation.jobs import (
    DAILY_REPORT,
    PAPER_REVIEW,
    QUEUE_KINDS,
    REFLECT_JOB,
    RL_TRAIN,
    shadow_record_hook,
)
from trader.features.evaluation.migrations import MIGRATIONS
from trader.features.evaluation.ports import PORT
from trader.features.evaluation.session_kinds import AGENTS, REFLECT
from trader.features.evaluation.settings import SCHEDULE_SECTIONS, SECTIONS
from trader.features.evaluation.status import STATUS_SECTION
from trader.features.evaluation.telegram import DAILY
from trader.features.evaluation.tool_cli import TOOLS
from trader.kernel.registry.types import Feature, Hook

FEATURE = Feature(
    id="evaluation",
    tier="service",
    sections=(*SECTIONS, *SCHEDULE_SECTIONS),
    migrations=MIGRATIONS,
    cli=evaluation_app,
    control_namespaces=("memory_breaker", "vix_last"),
    ports=(PORT,),
    jobs=(DAILY_REPORT, PAPER_REVIEW, REFLECT_JOB, RL_TRAIN),
    queue_kinds=QUEUE_KINDS,
    session_kinds=(REFLECT,),
    agents=AGENTS,
    tools=TOOLS,
    telegram_commands=(DAILY,),
    status_sections=(STATUS_SECTION,),
    hooks=(Hook("scan_analyze.post", shadow_record_hook),),
)
