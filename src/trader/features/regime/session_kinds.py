"""regime 의 LLM 세션 종류(`market`)와 에이전트 정의.

에이전트 이름은 전역 유일이라 리서처는 `market-researcher-*`, 오케스트레이터는
`market-orchestrator` 다. 커널 `orchestrator_prompt` 는 이름이 `orchestrator` 인 정의를 읽으므로
러너에 넘기는 매핑(`agents_for_runner`)에만 별칭 `orchestrator` 를 더한다."""

from __future__ import annotations

from pathlib import Path

from trader.features.regime.fake import run_fake_market
from trader.features.regime.schemas import AnalystReport, DebateTurn, HedgeProposal
from trader.kernel.registry.types import AgentDef, SessionKind
from trader.kernel.session.agents import ORCHESTRATOR, by_name

MARKET_AGENTS = (
    "analyst-market",
    "market-researcher-bull",
    "market-researcher-bear",
    "strategist",
)

_DIR = Path(__file__).parent / "agents"
_ORCH_MD = _DIR / "market-orchestrator.md"


def _schema(name: str) -> type:
    if name.startswith("analyst-"):
        return AnalystReport
    if name.startswith("market-researcher-"):
        return DebateTurn
    return HedgeProposal


AGENTS = (
    *(AgentDef(n, _DIR / f"{n}.md", _schema(n)) for n in MARKET_AGENTS),
    AgentDef("market-orchestrator", _ORCH_MD),
)

MARKET = SessionKind(
    kind="market",
    agents=MARKET_AGENTS,
    prompt_section="market",
    schemas=(AnalystReport, DebateTurn, HedgeProposal),
    timeout_key="market",
    runs_kind="market",
    extra_cap_key="market_session_cap",
    throttle="hard",
    fake=run_fake_market,
    retry_guard=False,
)


def agents_for_runner() -> dict[str, AgentDef]:
    """`run_session(..., agents=)` 용 매핑: AGENTS + 별칭 `orchestrator`(market-orchestrator.md)."""
    return {**by_name(AGENTS), ORCHESTRATOR: AgentDef(ORCHESTRATOR, _ORCH_MD)}
