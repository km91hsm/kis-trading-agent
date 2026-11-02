"""에이전트 정의(md) 로더와 `claude --agents` JSON·오케스트레이터 프롬프트 조립.

md 형식: YAML 프런트매터(name, description, model, tools) + 마크다운 본문.
커널은 기능 이름을 모른다 — 어떤 에이전트가 있는지는 `AgentDef`, 세션이 쓰는 에이전트와 프롬프트
절은 `SessionKind` 가 정한다. 모델은 `models` 매핑이 md 기본보다 우선한다.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import yaml

from trader.kernel.config import ConfigError
from trader.kernel.platform import bundle
from trader.kernel.registry.types import AgentDef, SessionKind

log = logging.getLogger("trader.session")

ORCHESTRATOR = "orchestrator"
MODE_HEADING = re.compile(r'^## \w+ 모드 \(`kind: "(\w+)"`\)', re.M)
_TOOL_CMD = "python -m trader tool"


@dataclass
class Agent:
    name: str
    description: str
    model: str
    prompt: str


def by_name(defs: Iterable[AgentDef]) -> dict[str, AgentDef]:
    """AgentDef 들을 이름으로 색인한다. 같은 이름이 둘이면 ConfigError."""
    out: dict[str, AgentDef] = {}
    for d in defs:
        if d.name in out:
            raise ConfigError(f"에이전트 이름 중복: {d.name}")
        out[d.name] = d
    return out


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return {}, text
    head = parts[0].lstrip("-").strip()
    body = parts[1].lstrip("\n")
    try:
        meta = yaml.safe_load(head) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"에이전트 프런트매터 구문 오류: {e}") from e
    return (meta if isinstance(meta, dict) else {}), body


def load_agent(defn: AgentDef) -> Agent:
    try:
        text = defn.path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"에이전트 정의 없음: {defn.name}") from None
    meta, body = split_frontmatter(text)
    return Agent(
        name=str(meta.get("name") or defn.name),
        description=str(meta.get("description") or ""),
        model=str(meta.get("model") or "sonnet"),
        prompt=body.strip() + "\n",
    )


def model_for(name: str, models: Mapping[str, str], default: str) -> str:
    key = "analyst" if name.startswith("analyst-") else name.replace("-", "_")
    return models.get(key, default)


def json_schema(model: type) -> str:
    """pydantic 모델의 JSON 스키마(compact, 한글 유지)."""
    return json.dumps(
        model.model_json_schema(),  # type: ignore[attr-defined]
        ensure_ascii=False,
        separators=(",", ":"),
    )


def with_schema(prompt: str, schema: type | None) -> str:
    """md 에 스키마 전문이 없으면 정본 모델에서 만들어 덧붙인다 (md 사본은 설명용)."""
    if schema is None or '"type":"object"' in prompt:
        return prompt
    return prompt.rstrip("\n") + "\n\n스키마: " + json_schema(schema) + "\n"


def _def_of(agents: Mapping[str, AgentDef], name: str) -> AgentDef:
    try:
        return agents[name]
    except KeyError:
        raise ConfigError(f"에이전트 정의 없음: {name}") from None


def agents_json(
    kind: SessionKind, agents: Mapping[str, AgentDef], models: Mapping[str, str]
) -> dict[str, dict]:
    """`claude --agents <json>` 형식: {name: {description, prompt, tools, model}}."""
    out: dict[str, dict] = {}
    for n in kind.agents:
        defn = _def_of(agents, n)
        a = load_agent(defn)
        out[n] = {
            "description": a.description,
            "prompt": with_schema(a.prompt, defn.schema),
            "tools": [],
            "model": model_for(n, models, a.model),
        }
    return out


def _body_for_section(body: str, section: str) -> str:
    """`## <x> 모드` 절 중 section 것만 남긴다(빈 section 이면 모드 절을 모두 뺀다)."""
    out: list[str] = []
    skip = False
    for line in body.splitlines(keepends=True):
        m = MODE_HEADING.match(line)
        if m:
            skip = m.group(1) != section
        elif line.startswith("# ") or line.startswith("## "):
            skip = False
        if not skip:
            out.append(line)
    return "".join(out)


def orchestrator_prompt(
    kind: SessionKind,
    agents: Mapping[str, AgentDef],
    *,
    debate_rounds: int,
    checkpoint_names: Sequence[str],
) -> str:
    """orchestrator md 본문(이 kind 의 모드 절만) + 런타임 설정(토론 라운드·체크포인트·스키마).

    md 의 `python -m trader tool` 은 실제 도구 명령(`bundle.tool_command()`)으로 바꾼다 —
    `--allowedTools` 와 같은 문자열이어야 LLM 이 도구를 부를 수 있다."""
    body = _body_for_section(
        load_agent(_def_of(agents, ORCHESTRATOR)).prompt, kind.prompt_section
    ).replace(_TOOL_CMD, bundle.tool_command())
    runtime = (
        "\n# 런타임 설정\n"
        f"- debate_rounds={debate_rounds} (라운드 0 독립 의견 뒤 반박 라운드 수)\n"
        "- 체크포인트 이름: " + ", ".join(checkpoint_names) + "\n"
    )
    for s in kind.schemas:
        runtime += f"- {s.__name__} 스키마: " + json_schema(s) + "\n"
    return body + runtime


def common_rules(prompt: str) -> list[str]:
    """`# 공통 규칙` 절의 번호 줄을 괄호 보충 앞까지 잘라 돌려준다."""
    tail = prompt.split("# 공통 규칙", 1)[1]
    return [ln.split("(", 1)[0].strip() for ln in tail.splitlines() if re.match(r"^\d\. ", ln)]
