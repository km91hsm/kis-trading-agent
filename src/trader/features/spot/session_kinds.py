"""spot 의 LLM 세션 종류와 에이전트 정의(analyze·resume). 다른 세션(reflect·…)은 후속 마일스톤."""

from __future__ import annotations

from pathlib import Path

from trader.features.spot.fake import run_fake_analyze, run_fake_resume
from trader.features.spot.research import RESEARCH
from trader.features.spot.schemas import AnalystReport, DebateTurn, Decision
from trader.kernel.registry.types import AgentDef, SessionKind
from trader.kernel.session.run_store import run_dir

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
    research_targets=lambda ctx, spec: [
        (run_dir(spec.date, spec.market, t["ticker"], ctx.state), RESEARCH) for t in spec.tickers
    ],
    retry_guard=True,  # 같은 종목 반복 타임아웃 차단(옛 analyze 동작)
)

RESUME = SessionKind(
    kind="resume",
    agents=AGENT_NAMES,
    prompt_section="resume",
    schemas=(AnalystReport, DebateTurn, Decision),
    timeout_key="resume",
    runs_kind="resume",
    extra_cap_key=None,
    throttle="soft",  # 보유 재평가는 soft 감속에서도 허용(kernel.session.usage.RESUME_KINDS)
    fake=run_fake_resume,
    stdin_extra=lambda spec: {"trigger": spec.extra["trigger"]},
    retry_guard=False,
)
