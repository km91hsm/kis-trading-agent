"""Feature-defined research contracts work without kernel knowledge of lane names."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from trader.kernel.config import LlmSettings
from trader.kernel.session.run_store import checkpoint_payload, write_checkpoint


def test_arbitrary_feature_defines_stages_and_model_metadata(conn, tmp_path):
    from trader.kernel.registry.types import ResearchSpec
    from trader.kernel.session import research_policy as p

    spec = ResearchSpec(
        scope="lunar",
        input_stage="lunar_input",
        report_prefix="lunar_report_",
        turn_prefix="lunar_turn_r",
        cross_turn_prefix="lunar_cross_r",
        completed_stage="lunar_decision",
        summary_stage="lunar_summary",
        cli_template="lunar debate --round <n>",
        bull_agent="lunar-optimist",
        bear_agent="lunar-skeptic",
        metadata=lambda directory, inputs: {
            "market": "US",
            "ticker": "MOON",
            "currency": "USD",
            "asof": inputs["when"],
        },
    )
    settings = SimpleNamespace(llm=LlmSettings(codex_debate={"enabled": True}), debate_rounds=0)
    p.start(conn, settings, tmp_path, spec, now=datetime(2026, 11, 7, tzinfo=UTC))
    write_checkpoint(
        tmp_path, "lunar_input", checkpoint_payload({"when": "2026-11-07T00:00:00+00:00"})
    )
    write_checkpoint(tmp_path, "lunar_report_1", checkpoint_payload({"source": "independent"}))
    data, _ = p.payload(conn, settings, tmp_path, spec, 0)
    assert (data["scope"], data["market"], data["ticker"], data["currency"]) == (
        "lunar",
        "US",
        "MOON",
        "USD",
    )
    assert data["asof"] == "2026-11-07T00:00:00+00:00" and data["reports"] == [
        {"source": "independent"}
    ]


def test_runner_uses_feature_cli_and_bear_agent(conn, project, monkeypatch):
    import json
    from pathlib import Path

    from tests.kernel.session.test_runner import AGENTS, _kind, _ok_result, make_ctx, spec
    from trader.kernel.registry.types import AgentDef, ResearchSpec
    from trader.kernel.session.runner import run_session

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    research = ResearchSpec(
        scope="lunar",
        input_stage="input",
        report_prefix="report_",
        turn_prefix="turn_r",
        cross_turn_prefix="cross_r",
        completed_stage="decision",
        summary_stage="summary",
        cli_template="lunar debate --round <n>",
        bull_agent="alpha",
        bear_agent="lunar-skeptic",
        metadata=lambda d, i: {},
    )
    kind = _kind(
        agents=("alpha", "lunar-skeptic"),
        research_targets=lambda ctx, spec: [(ctx.state, research)],
    )
    ctx = make_ctx(project, conn, llm=LlmSettings(codex_debate={"enabled": True}))
    agents = {**AGENTS, "lunar-skeptic": AgentDef("lunar-skeptic", AGENTS["alpha"].path)}
    captured = {}

    def fake(cmd, **kw):
        captured["prompt"] = cmd[cmd.index("--append-system-prompt") + 1]
        captured["agents"] = json.loads(
            Path(cmd[cmd.index("--agents") + 1]).read_text(encoding="utf-8")
        )
        return _ok_result()

    result = run_session(
        spec(), kind, ctx, agents=agents, runner=fake, claude_bin=Path("/fake/claude")
    )
    assert not result.ok and "Codex" in result.error
    assert "lunar debate --round <n>" in captured["prompt"]
    assert "alpha" in captured["agents"] and "lunar-skeptic" not in captured["agents"]


def test_turn_stage_names_lanes():
    from trader.kernel.registry.types import ResearchSpec

    spec = ResearchSpec(
        scope="lunar",
        input_stage="i",
        report_prefix="r_",
        turn_prefix="lunar_turn_r",
        cross_turn_prefix="lunar_cross_r",
        completed_stage="d",
        summary_stage="s",
        cli_template="x",
        bull_agent="a",
        bear_agent="b",
        metadata=lambda d, i: {},
    )
    assert spec.turn_stage("a", 0, "bull") == "lunar_turn_r0_bull"
    assert spec.turn_stage("b", 1, "bear") == "lunar_cross_r1_bear"
    with pytest.raises(ValueError):
        spec.turn_stage("c", 0, "bull")


def test_feature_cross_prefixes():
    from trader.features.fo.research import RESEARCH as FO
    from trader.features.regime.research import RESEARCH as MARKET
    from trader.features.spot.research import RESEARCH as SPOT

    assert SPOT.turn_stage("b", 0, "bull") == "20_debatex_r0_bull"
    assert MARKET.turn_stage("b", 0, "bull") == "_market_debatex_r0_bull"
    assert FO.turn_stage("b", 0, "bull") == "_fo_debatex_r0_bull"
