"""evaluation 의 에이전트 정의와 LLM 세션 종류 `REFLECT`.

커널 `orchestrator_prompt` 는 이름이 `orchestrator` 인 정의를 읽으므로 러너에 넘기는 매핑
(`agents_for_runner`)에만 별칭 `orchestrator` 를 더한다."""

from __future__ import annotations

from pathlib import Path

from trader.features.evaluation.fake import run_fake_reflect
from trader.features.evaluation.schemas import Reflection
from trader.kernel.registry.types import AgentDef, SessionKind
from trader.kernel.session.agents import ORCHESTRATOR, by_name

_DIR = Path(__file__).parent / "agents"

AGENTS = (
    AgentDef("reflector", _DIR / "reflector.md", Reflection),
    AgentDef("reflect-orchestrator", _DIR / "reflect-orchestrator.md", None),
)

# 이 기능의 세션이 부를 수 있는 `trader tool` 명령(도구 진입점이 강제한다)
TOOLS = ("submit-reflection",)

REFLECT = SessionKind(
    tools=TOOLS,
    kind="reflect",
    agents=("reflector",),
    prompt_section="reflect",
    schemas=(Reflection,),
    timeout_key="reflect",
    runs_kind="reflect",
    extra_cap_key=None,
    throttle="hard",
    fake=run_fake_reflect,
    stdin_extra=lambda spec: {"entries": spec.extra["entries"]},
    retry_guard=False,
)


def agents_for_runner() -> dict[str, AgentDef]:
    """`run_session(..., agents=)` 용 매핑: AGENTS + 별칭 `orchestrator`(reflect-orchestrator)."""
    return {
        **by_name(AGENTS),
        ORCHESTRATOR: AgentDef(ORCHESTRATOR, _DIR / "reflect-orchestrator.md", None),
    }
