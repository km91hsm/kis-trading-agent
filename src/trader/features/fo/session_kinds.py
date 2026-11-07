"""fo 의 LLM 세션 종류(`fo`·`fo_resume`)와 에이전트 정의(A6).

fo 가 가진 md 는 `fo-trader`·`fo-orchestrator` 둘뿐이다. 애널리스트·리서처
(`analyst-technical`·`analyst-sentiment`·`researcher-bull`·`researcher-bear` 는 spot,
`analyst-market` 은 regime)는 레인이 그 기능을 import 하지 않고 **이름으로 재사용**한다 —
`agents_for_runner()` 가 커널 레지스트리(`discover()`)에 등록된 모든 기능의 `Feature.agents` 를
이름으로 모은다. 커널 `orchestrator_prompt` 는 이름이 `orchestrator` 인 정의를 읽으므로 러너
매핑에만 별칭 `orchestrator` → fo-orchestrator.md 를 둔다(regime 선례). 상한
(`fo.session_cap`)은 커널이 모르는 fo 설정이라 `extra_cap_key=None` 이고 `fo/session.run` 이
세션 앞에서 검사한다.
"""

from __future__ import annotations

from pathlib import Path

from trader.features.fo.fake import run_fake_fo
from trader.features.fo.research import RESEARCH
from trader.features.fo.schemas import FoDecisionEnvelope
from trader.kernel.registry.types import AgentDef, SessionKind
from trader.kernel.session.agents import ORCHESTRATOR
from trader.kernel.session.run_store import market_dir
from trader.kernel.session.schemas import AnalystReport, DebateTurn

FO_AGENTS = (
    "analyst-technical",
    "analyst-sentiment",
    "analyst-market",
    "researcher-bull",
    "researcher-bear",
    "fo-trader",
)

_DIR = Path(__file__).parent / "agents"
_ORCH_MD = _DIR / "fo-orchestrator.md"

FO_TRADER = AgentDef("fo-trader", _DIR / "fo-trader.md", FoDecisionEnvelope)
FO_ORCHESTRATOR = AgentDef("fo-orchestrator", _ORCH_MD)
AGENTS = (FO_TRADER, FO_ORCHESTRATOR)

FO = SessionKind(
    kind="fo",
    research_targets=lambda ctx, spec: [(market_dir(spec.date, spec.market, ctx.state), RESEARCH)],
    agents=FO_AGENTS,
    prompt_section="fo",
    schemas=(AnalystReport, DebateTurn, FoDecisionEnvelope),
    timeout_key="fo",
    runs_kind="fo",
    extra_cap_key=None,
    throttle="hard",
    fake=run_fake_fo,
    stdin_extra=None,
    retry_guard=False,
)

FO_RESUME = SessionKind(
    kind="fo_resume",
    agents=FO_AGENTS,
    prompt_section="fo_resume",
    schemas=(FoDecisionEnvelope,),
    timeout_key="fo_resume",
    runs_kind="monitor",
    extra_cap_key=None,
    throttle="soft",
    fake=run_fake_fo,
    stdin_extra=lambda spec: {"position": spec.extra["position"], "trigger": spec.extra["trigger"]},
    retry_guard=False,
)


def agents_for_runner() -> dict[str, AgentDef]:
    """`run_session(..., agents=)` 용 매핑: 등록된 모든 기능의 에이전트(이름 → 정의) + 별칭
    `orchestrator`(fo-orchestrator.md). spot 의 `orchestrator` 정의는 이 별칭이 덮는다."""
    from trader.kernel.registry import discover

    out: dict[str, AgentDef] = {}
    for f in discover():
        for d in f.agents:
            out.setdefault(d.name, d)
    for d in AGENTS:
        out[d.name] = d
    out[ORCHESTRATOR] = AgentDef(ORCHESTRATOR, _ORCH_MD)
    return out
