"""kernel/session/agents: md 로더·agents_json·orchestrator_prompt."""

from pathlib import Path

import pytest

from trader.kernel.config import ConfigError
from trader.kernel.platform import bundle
from trader.kernel.registry.types import AgentDef, SessionKind
from trader.kernel.session import agents as ag

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "agents"


def _defs(schema=None) -> dict[str, AgentDef]:
    return ag.by_name(
        [
            AgentDef("alpha", FIX / "alpha.md", schema),
            AgentDef("orchestrator", FIX / "orchestrator.md"),
        ]
    )


def _kind(section="", agents=("alpha",), schemas=()) -> SessionKind:
    return SessionKind(
        kind="k",
        agents=agents,
        prompt_section=section,
        schemas=schemas,
        timeout_key="t",
        runs_kind="k",
        extra_cap_key=None,
        throttle="soft",
        fake=lambda *a, **k: None,
    )


def _schema_model():
    from pydantic import BaseModel

    class Out(BaseModel):
        answer: str

    return Out


def test_by_name_duplicate_is_config_error():
    d = AgentDef("alpha", FIX / "alpha.md")
    assert ag.by_name([d]) == {"alpha": d}
    with pytest.raises(ConfigError):
        ag.by_name([d, d])


def test_agents_json_uses_settings_models():
    j = ag.agents_json(_kind(), _defs(), {"alpha": "opus"})
    assert list(j) == ["alpha"]
    assert j["alpha"]["model"] == "opus" and j["alpha"]["tools"] == []
    assert j["alpha"]["description"] and j["alpha"]["prompt"]
    j2 = ag.agents_json(_kind(), _defs(), {})
    assert j2["alpha"]["model"] == "haiku"  # md 기본


def test_model_for_mapping():
    assert ag.model_for("analyst-technical", {"analyst": "x"}, "d") == "x"
    assert ag.model_for("fo-trader", {"fo_trader": "y"}, "d") == "y"
    assert ag.model_for("zzz", {}, "d") == "d"


def test_orchestrator_prompt_has_procedure(monkeypatch):
    monkeypatch.setattr(bundle, "is_frozen", lambda: False)
    p = ag.orchestrator_prompt(
        _kind("resume"), _defs(), debate_rounds=2, checkpoint_names=["a", "b"]
    )
    assert "python -m trader tool status" in p
    assert "debate_rounds=2" in p and "a" in p and "b" in p


def test_missing_agent_config_error(tmp_path):
    with pytest.raises(ConfigError, match="에이전트 정의 없음: nope"):
        ag.load_agent(AgentDef("nope", tmp_path / "nope.md"))
    with pytest.raises(ConfigError):
        ag.agents_json(_kind(agents=("ghost",)), _defs(), {})


def test_frontmatter_parse(tmp_path):
    p = tmp_path / "x.md"
    p.write_text(
        "---\nname: x\ndescription: 설명\nmodel: sonnet\ntools: []\n---\n# 역할\n본문\n",
        encoding="utf-8",
    )
    a = ag.load_agent(AgentDef("x", p))
    assert a.description == "설명" and a.prompt.startswith("# 역할") and a.model == "sonnet"
    p.write_text("---\ndescription: [\n---\nx", encoding="utf-8")
    with pytest.raises(ConfigError):
        ag.load_agent(AgentDef("x", p))
    p.write_text("본문만", encoding="utf-8")
    b = ag.load_agent(AgentDef("fallback", p))
    assert b.name == "fallback" and b.model == "sonnet"


def test_agents_json_appends_schema_when_md_lacks_it():
    j = ag.agents_json(_kind(), _defs(_schema_model()), {})
    assert '"type":"object"' in j["alpha"]["prompt"]
    assert j["alpha"]["prompt"].count("스키마:") == 1
    # 이미 스키마가 있으면 다시 붙이지 않는다
    again = ag.with_schema(j["alpha"]["prompt"], _schema_model())
    assert again == j["alpha"]["prompt"]


def test_prompt_section_keeps_only_requested_mode():
    d = _defs()
    only = ag.orchestrator_prompt(_kind("resume"), d, debate_rounds=1, checkpoint_names=[])
    assert "재개 절차 본문" in only and "다른 모드 본문" not in only
    none = ag.orchestrator_prompt(_kind(""), d, debate_rounds=1, checkpoint_names=[])
    assert "재개 절차 본문" not in none and "다른 모드 본문" not in none
    assert "# 절차" in none


def test_prompt_rewrites_tool_command_when_frozen(monkeypatch):
    monkeypatch.setattr(bundle, "is_frozen", lambda: True)
    p = ag.orchestrator_prompt(_kind("resume"), _defs(), debate_rounds=1, checkpoint_names=[])
    assert "trader tool status" in p and "python -m trader" not in p


def test_orchestrator_prompt_appends_kind_schemas():
    p = ag.orchestrator_prompt(
        _kind("resume", schemas=(_schema_model(),)), _defs(), debate_rounds=1, checkpoint_names=[]
    )
    assert '"answer"' in p


def test_common_rules_extracts_six_prefixes():
    rules = ag.common_rules(ag.load_agent(_defs()["alpha"]).prompt)
    assert len(rules) == 6
    assert rules[0].startswith("1. 제공된 데이터만")
    assert all("(" not in r for r in rules)


def test_common_rules_identical_prefix_across_fixtures():
    d = _defs()
    a, o = (ag.common_rules(ag.load_agent(x).prompt) for x in d.values())
    assert a == o and len(a) == 6


def test_common_rules_strips_trailing_period_before_or_after_parenthesis(tmp_path):
    """끝 마침표가 괄호 앞(`…한다. (보충)`)·뒤(`…한다 (보충).`)여도 같은 prefix 가 된다."""
    rule = "1. 제공된 데이터만 근거로 판단한다"
    tail = "\n".join(f"{i}. 규칙{i}" for i in range(2, 7))
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text(f"# 공통 규칙\n{rule}. (보충 A)\n{tail}\n", encoding="utf-8")
    b.write_text(f"# 공통 규칙\n{rule} (보충 B).\n{tail}\n", encoding="utf-8")
    c = tmp_path / "c.md"
    c.write_text(f"# 공통 규칙\n{rule}.\n{tail}\n", encoding="utf-8")
    got = {tuple(ag.common_rules(ag.load_agent(AgentDef(p.stem, p)).prompt)) for p in (a, b, c)}
    assert len(got) == 1
    assert next(iter(got))[0] == rule
