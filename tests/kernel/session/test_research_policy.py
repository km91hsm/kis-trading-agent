"""Frozen policy cannot be bypassed by missing turns, changed inputs or forged provenance."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from tests.features.spot.helpers import ok_report, ok_turn
from trader.features.fo.research import RESEARCH as FO_RESEARCH
from trader.features.regime.research import RESEARCH as MARKET_RESEARCH
from trader.features.spot.research import RESEARCH as SPOT_RESEARCH
from trader.kernel.config import LlmSettings
from trader.kernel.session.run_store import checkpoint_payload, read_checkpoint, write_checkpoint

RESEARCHES = {"spot": SPOT_RESEARCH, "market": MARKET_RESEARCH, "fo": FO_RESEARCH}

NOW = datetime(2026, 11, 7, tzinfo=UTC)


def settings(enabled=True):
    return SimpleNamespace(llm=LlmSettings(codex_debate={"enabled": enabled}), debate_rounds=0)


def seed(d):
    d.mkdir(exist_ok=True)
    write_checkpoint(
        d, "00_inputs", {"meta": {"market": "KR", "ticker": "005930", "asof": NOW.isoformat()}}
    )
    for role in ("technical", "sentiment"):
        write_checkpoint(d, f"10_analyst_{role}", checkpoint_payload(ok_report(role=role)))


def test_policy_frozen_and_mandatory_rounds(conn, tmp_path):
    from trader.kernel.session import research_policy as p

    s = settings()
    policy = p.start(conn, s, tmp_path, SPOT_RESEARCH, now=NOW)
    assert policy["enabled"] and policy["rounds"] == 1
    seed(tmp_path)
    s.llm.codex_debate.enabled = False
    assert p.get(conn, s, tmp_path, SPOT_RESEARCH)["enabled"]
    err = p.completion_error(conn, s, tmp_path, SPOT_RESEARCH)
    assert "Codex" in err and "0" in err
    with pytest.raises(p.ResearchPolicyError, match="재분석"):
        p.start(conn, s, tmp_path, SPOT_RESEARCH, now=NOW)
    assert read_checkpoint(tmp_path, "00_inputs") is not None


def test_new_enabled_policy_rejects_legacy_unfinished_but_preserves_decision(conn, tmp_path):
    from trader.kernel.session import research_policy as p

    seed(tmp_path)
    with pytest.raises(p.ResearchPolicyError, match="재분석"):
        p.start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW)
    write_checkpoint(tmp_path, "30_decision", checkpoint_payload({"action": "HOLD"}))
    policy = p.start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW)
    assert not policy["enabled"] and policy["legacy_completed"]
    assert read_checkpoint(tmp_path, "30_decision")["data"]["action"] == "HOLD"


def test_general_checkpoint_cannot_forge_codex_bear(conn, tmp_path):
    from trader.features.spot import tools
    from trader.kernel.session import research_policy as p

    s = settings()
    d = tmp_path / "runs" / "2026-11-07" / "KR" / "005930"
    d.mkdir(parents=True)
    p.start(conn, s, d, SPOT_RESEARCH, now=NOW)
    seed(d)
    ctx = tools.ToolContext(settings=s, accounts=[], conn=conn, state=tmp_path, now=lambda: NOW)
    out = tools.checkpoint_write(
        ctx,
        ticker="005930",
        market="KR",
        date=date(2026, 11, 7),
        stage="20_debate_r0_bear",
        payload=ok_turn("bear", 0),
        call_meta={"provider": "codex"},
    )
    assert not out["ok"] and "Codex" in out["error"]
    assert read_checkpoint(d, "20_debate_r0_bear") is None
    # Even complete-looking files without matching Python call records are rejected.
    for rnd in (0, 1):
        for side in ("bull", "bear"):
            write_checkpoint(
                d,
                f"20_debate_r{rnd}_{side}",
                checkpoint_payload(ok_turn(side, rnd), {"provider": "codex"}),
            )
    assert "증명" in p.completion_error(conn, s, d, SPOT_RESEARCH)
    assert not tools.checkpoint_summarize(
        ctx, ticker="005930", market="KR", date=date(2026, 11, 7)
    )["ok"]


@pytest.mark.parametrize("scope", ["spot", "market", "fo"])
def test_actual_bridge_rounds_complete_and_changed_turn_rejected(
    conn, tmp_path, monkeypatch, scope
):
    import json
    import subprocess
    from pathlib import Path

    from trader.kernel.session import research_policy as p

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    s = settings()
    d = tmp_path
    p.start(conn, s, d, RESEARCHES[scope], now=NOW)
    r = RESEARCHES[scope]
    input_stage, analyst_prefix, prefix = r.input_stage, r.report_prefix, r.turn_prefix
    write_checkpoint(
        d,
        input_stage,
        checkpoint_payload({"market": "KR", "proxy": "005930", "asof": NOW.isoformat()}),
    )
    for role in ("technical", "sentiment"):
        stage = analyst_prefix + role if scope != "market" else analyst_prefix
        write_checkpoint(d, stage, checkpoint_payload(ok_report(role=role)))
    ctx = SimpleNamespace(conn=conn, settings=s, now=lambda: NOW)

    def turn(side, rnd):
        return ok_turn(
            side,
            rnd,
            issues=[
                {
                    "issue_id": f"{side}:case",
                    "topic": "독립 쟁점",
                    "claim": "근거 주장",
                    "evidence": ["technical.indicators"],
                }
            ],
            responses=[
                {
                    "issue_id": f"{'bull' if side == 'bear' else 'bear'}:case",
                    "response": "상대 주제 반박",
                    "evidence": ["technical.indicators"],
                }
            ]
            if rnd
            else [],
            rebuttals=["상호반박"] if rnd else [],
        )

    def fake(cmd, **kw):
        data = json.loads(kw["input"].split("\n", 1)[1])
        if data["round"] == 0:
            assert data["opponent_previous"] is None and data["own_previous"] is None
        else:
            assert data["opponent_previous"]["side"] == "bull"
        response = turn("bear", data["round"])
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text(json.dumps(response))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    for rnd in (0, 1):
        assert p.write_error(conn, s, d, RESEARCHES[scope], round_no=rnd, side="bull") is None
        write_checkpoint(
            d, f"{prefix}{rnd}_bull", checkpoint_payload(turn("bull", rnd), {"provider": "claude"})
        )
        out = p.generate_turn(
            ctx, d, RESEARCHES[scope], rnd, runner=fake, bin_path=Path("/fake/codex")
        )
        assert out["ok"], out
    assert p.completion_error(conn, s, d, RESEARCHES[scope]) is None
    write_checkpoint(
        d,
        f"{prefix}0_bull",
        checkpoint_payload({**turn("bull", 0), "arguments": ["changed"]}, {"provider": "claude"}),
    )
    assert "증명" in p.completion_error(conn, s, d, RESEARCHES[scope])


def test_on_debate_requires_symmetric_issue_coverage(conn, tmp_path):
    from trader.kernel.session import research_policy as p

    s = settings()
    p.start(conn, s, tmp_path, SPOT_RESEARCH, now=NOW)
    seed(tmp_path)
    # Invalid independent turn: no independently proposed issues.
    assert "쟁점" in p.turn_error(conn, s, tmp_path, SPOT_RESEARCH, ok_turn("bull", 0))
    issue = {
        "issue_id": "bull:valuation",
        "topic": "밸류에이션",
        "claim": "가격이 과도함",
        "evidence": ["technical.indicators"],
    }
    bull = ok_turn("bull", 0, issues=[issue], responses=[])
    assert p.turn_error(conn, s, tmp_path, SPOT_RESEARCH, bull) is None
    write_checkpoint(
        tmp_path, "20_debate_r0_bull", checkpoint_payload(bull, {"provider": "claude"})
    )
    bear_issue = {**issue, "issue_id": "bear:cycle", "topic": "공급사이클"}
    bear = ok_turn("bear", 0, issues=[bear_issue], responses=[])
    write_checkpoint(tmp_path, "20_debate_r0_bear", checkpoint_payload(bear, {"provider": "codex"}))
    r1 = ok_turn("bull", 1, issues=[issue], rebuttals=["반박"], responses=[])
    assert "coverage" in p.turn_error(conn, s, tmp_path, SPOT_RESEARCH, r1)
    r1["responses"] = [
        {
            "issue_id": "bear:cycle",
            "response": "공급위험이 가격에 반영",
            "evidence": ["technical.indicators"],
        }
    ]
    assert p.turn_error(conn, s, tmp_path, SPOT_RESEARCH, r1) is None
