"""fo 기능 선언 (레인): 설정 절·마이그레이션 14·control 네임스페이스·세션 종류·에이전트·도구·
데몬 잡 3개(`kr_fo_session`·`kr_fo_monitor`·`kr_fo_reconcile`)·큐 kind 4개(A5).

CLI·텔레그램·상태 절은 뒤 Task 가 채운다."""

from trader.features.fo.jobs import JOBS, QUEUE_KINDS
from trader.features.fo.migrations import MIGRATIONS
from trader.features.fo.session_kinds import AGENTS, FO, FO_RESUME
from trader.features.fo.settings import SECTIONS
from trader.features.fo.tools import (
    FO_SUBMIT_RULE,
    FO_WRITE_RULES,
    fo_tool_app,
    submit_fo_decision_cmd,
)
from trader.kernel.registry.types import Feature, ToolCommand

# 옛 control 플래그 키 접두(A4): `fo_verified:<env>:<acct>:<product>:<vehicle>` 등
CONTROL_NAMESPACES = (
    "fo_verified", "fo_halted", "fo_day_open", "fo_alerts", "fo_ref_iv", "fo_pending_entry",
    "fo_replace",
)  # fmt: skip

TOOLS = (
    ToolCommand("fo", fo_tool_app, FO_WRITE_RULES),
    ToolCommand("submit-fo-decision", submit_fo_decision_cmd, (FO_SUBMIT_RULE,)),
)

FEATURE = Feature(
    id="fo", tier="lane", sections=SECTIONS, migrations=MIGRATIONS,
    control_namespaces=CONTROL_NAMESPACES, session_kinds=(FO, FO_RESUME), agents=AGENTS,
    tools=TOOLS, jobs=JOBS, queue_kinds=QUEUE_KINDS,
)  # fmt: skip
