"""spot 의 LLM 세션 종류와 에이전트 정의(analyze). 다른 세션(resume·reflect·…)은 후속 마일스톤."""

from __future__ import annotations

from pathlib import Path

from trader.features.spot.fake import run_fake_analyze
from trader.features.spot.schemas import AnalystReport, DebateTurn, Decision
from trader.kernel.registry.types import AgentDef, SessionKind

AGENT_NAMES = (
    "analyst-technical",
    "analyst-fundamental",
    "analyst-news-macro",
    "analyst-sentiment",
    "researcher-bull",
    "researcher-bear",
    "trader",
)

_DIR = Path(__file__).parent / "agents"


def _schema(name: str) -> type:
    if name.startswith("analyst-"):
        return AnalystReport
    if name.startswith("researcher-"):
        return DebateTurn
    return Decision


AGENTS = (
    *(AgentDef(n, _DIR / f"{n}.md", _schema(n)) for n in AGENT_NAMES),
    AgentDef("orchestrator", _DIR / "orchestrator.md", None),
)

ANALYZE = SessionKind(
    kind="analyze",
    agents=AGENT_NAMES,
    prompt_section="",
    schemas=(AnalystReport, DebateTurn, Decision),
    timeout_key="analyze",
    runs_kind="analyze",
    extra_cap_key=None,
    throttle="hard",
    fake=run_fake_analyze,
    stdin_extra=None,
    retry_guard=True,  # 같은 종목 반복 타임아웃 차단(옛 analyze 동작)
)
