"""scan 의 에이전트 정의(LLM 세션 종류 `SCREEN`·`DISCOVER` 는 Task 10 에서 더한다).

커널 `orchestrator_prompt` 는 이름이 `orchestrator` 인 정의를 읽으므로 러너에 넘기는 매핑
(`agents_for_runner`)에만 별칭 `orchestrator` 를 더한다."""

from __future__ import annotations

from pathlib import Path

from trader.features.scan.schemas import Discovered, Screened
from trader.kernel.registry.types import AgentDef
from trader.kernel.session.agents import ORCHESTRATOR, by_name

AGENT_NAMES = ("scout", "screener")

_DIR = Path(__file__).parent / "agents"
_ORCH_MD = _DIR / "scan-orchestrator.md"

AGENTS = (
    AgentDef("scout", _DIR / "scout.md", Discovered),
    AgentDef("screener", _DIR / "screener.md", Screened),
    AgentDef("scan-orchestrator", _ORCH_MD),
)


def agents_for_runner() -> dict[str, AgentDef]:
    """`run_session(..., agents=)` 용 매핑: AGENTS + 별칭 `orchestrator`(scan-orchestrator.md)."""
    return {**by_name(AGENTS), ORCHESTRATOR: AgentDef(ORCHESTRATOR, _ORCH_MD)}
