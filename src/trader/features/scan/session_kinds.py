"""scan 의 에이전트 정의와 LLM 세션 종류 `SCREEN`·`DISCOVER`.

커널 `orchestrator_prompt` 는 이름이 `orchestrator` 인 정의를 읽으므로 러너에 넘기는 매핑
(`agents_for_runner`)에만 별칭 `orchestrator` 를 더한다."""

from __future__ import annotations

from pathlib import Path

from trader.features.scan.fake import run_fake_discover, run_fake_screen
from trader.features.scan.schemas import Discovered, Screened
from trader.kernel.registry.types import AgentDef, SessionKind
from trader.kernel.session.agents import ORCHESTRATOR, by_name

AGENT_NAMES = ("scout", "screener")

_DIR = Path(__file__).parent / "agents"
_ORCH_MD = _DIR / "scan-orchestrator.md"

AGENTS = (
    AgentDef("scout", _DIR / "scout.md", Discovered),
    AgentDef("screener", _DIR / "screener.md", Screened),
    AgentDef("scan-orchestrator", _ORCH_MD),
)

SCREEN = SessionKind(
    kind="screen",
    agents=AGENT_NAMES,
    prompt_section="screen",
    schemas=(Screened, Discovered),
    timeout_key="screen",
    runs_kind="scan",
    extra_cap_key=None,
    throttle="hard",
    fake=run_fake_screen,
    stdin_extra=lambda spec: {
        "candidates": spec.extra["candidates"],
        **({"context": spec.extra["context"]} if spec.extra.get("context") else {}),
    },
)

DISCOVER = SessionKind(
    kind="discover",
    agents=("scout",),
    prompt_section="discover",
    schemas=(Discovered,),
    timeout_key="discover",
    runs_kind="scan",
    extra_cap_key=None,
    throttle="hard",
    fake=run_fake_discover,
)


def agents_for_runner() -> dict[str, AgentDef]:
    """`run_session(..., agents=)` 용 매핑: AGENTS + 별칭 `orchestrator`(scan-orchestrator.md)."""
    return {**by_name(AGENTS), ORCHESTRATOR: AgentDef(ORCHESTRATOR, _ORCH_MD)}
