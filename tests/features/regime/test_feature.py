"""regime 기능 선언·설정 템플릿."""

import json
from pathlib import Path

import yaml

from trader.kernel.platform.bundle import templates_dir


def test_feature_declares_sections_and_migration_v10():
    from trader.features.regime.feature import FEATURE
    from trader.features.regime.settings import BlackoutCfg, HedgeCfg

    assert FEATURE.id == "regime" and FEATURE.tier == "service"
    assert {(s.file, s.key, s.model) for s in FEATURE.sections} == {
        ("settings", "hedge", HedgeCfg),
        ("settings", "event_blackout", BlackoutCfg),
    }
    assert [(m.version, m.name, m.owner) for m in FEATURE.migrations] == [
        (10, "regime_market_views", "regime")
    ]


def test_settings_template_has_every_hedge_field(monkeypatch):
    from trader.features.regime.settings import BlackoutCfg, HedgeCfg

    monkeypatch.setenv("TRADER_HOME", str(Path(__file__).resolve().parents[3]))
    path = templates_dir() / "config" / "settings.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(raw["hedge"]) == set(HedgeCfg.model_fields)
    assert set(raw["event_blackout"]) == set(BlackoutCfg.model_fields)
    # 템플릿 값 = 모델 기본값(옛 값 그대로)
    assert HedgeCfg(**raw["hedge"]) == HedgeCfg()
    assert BlackoutCfg(**raw["event_blackout"]) == BlackoutCfg()


def test_feature_declares_market_session_tools_and_agents():
    """시장 세션: 도구 `market`·`submit-market-view`, SessionKind `market`, 에이전트 5개."""
    from trader.features.regime.feature import FEATURE
    from trader.features.regime.session_kinds import MARKET, MARKET_AGENTS, agents_for_runner

    assert [t.name for t in FEATURE.tools] == ["market", "submit-market-view"]
    assert FEATURE.session_kinds == (MARKET,)
    assert MARKET_AGENTS == (
        "analyst-market",
        "market-researcher-bull",
        "market-researcher-bear",
        "strategist",
    )
    assert [a.name for a in FEATURE.agents] == [*MARKET_AGENTS, "market-orchestrator"]
    assert (MARKET.kind, MARKET.timeout_key, MARKET.runs_kind) == ("market",) * 3
    assert MARKET.extra_cap_key == "market_session_cap" and MARKET.prompt_section == "market"
    runner_agents = agents_for_runner()
    assert set(runner_agents) == {*MARKET_AGENTS, "market-orchestrator", "orchestrator"}
    assert runner_agents["orchestrator"].path == runner_agents["market-orchestrator"].path


def test_market_orchestrator_prompt_has_only_market_mode():
    """오케스트레이터 프롬프트는 market 모드 절만 담고 리서처 이름은 market-researcher-*."""
    from trader.features.regime.session_kinds import MARKET, agents_for_runner
    from trader.kernel.session.agents import orchestrator_prompt

    p = orchestrator_prompt(MARKET, agents_for_runner(), debate_rounds=1, checkpoint_names=())
    assert "market bundle" in p and "submit-market-view" in p
    assert "market-researcher-bull" in p and "fo bundle" not in p
    assert "HedgeProposal 스키마" in p


def test_settings_template_has_strategist_model_and_market_timeout(monkeypatch):
    monkeypatch.setenv("TRADER_HOME", str(Path(__file__).resolve().parents[3]))
    raw = yaml.safe_load((templates_dir() / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert raw["models"]["strategist"] == "sonnet"
    assert raw["llm"]["timeout_by_kind"]["market"] == 600


def test_tool_cli_market_bundle_outputs_json_line(project, monkeypatch):
    """`trader tool market bundle` 은 JSON 1줄(입력 없으면 ok=false, 종료 코드 1)."""
    from typer.testing import CliRunner

    from trader.features.regime.tool_cli import market_app

    r = CliRunner().invoke(
        market_app, ["bundle", "--market", "KR", "--date", "2026-10-09", "--kind", "analyst"]
    )
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert r.exit_code == 1 and out["ok"] is False and "_market_inputs" in out["error"]
