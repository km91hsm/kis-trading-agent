"""A regenerated final turn must never reuse an older summary for a new decision."""

import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.features.spot.helpers import ok_report, ok_turn
from trader.features.fo.research import RESEARCH as FO_RESEARCH
from trader.features.regime.research import RESEARCH as MARKET_RESEARCH
from trader.features.spot.research import RESEARCH as SPOT_RESEARCH
from trader.kernel.config import LlmSettings
from trader.kernel.session import research_policy as p
from trader.kernel.session import tooling
from trader.kernel.session.run_store import (
    checkpoint_payload,
    market_dir,
    read_checkpoint,
    run_dir,
    write_checkpoint,
)

RESEARCHES = {"spot": SPOT_RESEARCH, "market": MARKET_RESEARCH, "fo": FO_RESEARCH}

NOW = datetime(2026, 11, 7, tzinfo=UTC)
DAY = date(2026, 11, 7)


def issue_turn(side, rnd, conviction=0.7):
    other = "bear" if side == "bull" else "bull"
    return ok_turn(
        side,
        rnd,
        conviction=conviction,
        issues=[
            {
                "issue_id": side + ":case",
                "topic": "독립주제",
                "claim": "근거",
                "evidence": ["technical.indicators"],
            }
        ],
        responses=[
            {
                "issue_id": other + ":case",
                "response": "상대주제 반박",
                "evidence": ["technical.indicators"],
            }
        ]
        if rnd
        else [],
        rebuttals=["상대주제 반박"] if rnd else [],
    )


@pytest.mark.parametrize("scope", ["spot", "market", "fo"])
def test_final_codex_regeneration_invalidates_summary_and_blocks_new_decision(
    conn, tmp_path, monkeypatch, scope
):
    from trader.features.fo import tools as fo
    from trader.features.regime import tools as market
    from trader.features.spot import tools as spot

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    settings = SimpleNamespace(llm=LlmSettings(codex_debate={"enabled": True}), debate_rounds=1)
    d = (
        run_dir(DAY, "KR", "005930", tmp_path)
        if scope == "spot"
        else market_dir(DAY, "KR", tmp_path, create=True)
    )
    p.start(conn, settings, d, RESEARCHES[scope], now=NOW, version=1)
    r = RESEARCHES[scope]
    input_stage, report_prefix, prefix = r.input_stage, r.report_prefix, r.turn_prefix
    inputs = {
        "meta": {"market": "KR", "ticker": "005930", "proxy": "005930", "asof": NOW.isoformat()},
        "market": "KR",
        "proxy": "005930",
        "asof": NOW.isoformat(),
    }
    write_checkpoint(d, input_stage, inputs if scope == "spot" else checkpoint_payload(inputs))
    for role in ("technical", "sentiment"):
        write_checkpoint(
            d,
            report_prefix + role if scope != "market" else report_prefix,
            checkpoint_payload(ok_report(role=role)),
        )
    ctx = SimpleNamespace(conn=conn, settings=settings, now=lambda: NOW)
    conviction = 0.7

    def fake(cmd, **kw):
        data = json.loads(kw["input"].split("\n", 1)[1])
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text(
            json.dumps(issue_turn("bear", data["round"], conviction)), encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    for rnd in (0, 1):
        assert (
            p.write_error(conn, settings, d, RESEARCHES[scope], round_no=rnd, side="bull") is None
        )
        write_checkpoint(
            d,
            f"{prefix}{rnd}_bull",
            checkpoint_payload(issue_turn("bull", rnd), {"provider": "claude"}),
        )
        assert p.generate_turn(
            ctx, d, RESEARCHES[scope], rnd, runner=fake, bin_path=Path("/fake/codex")
        )["ok"]
    summary_stage = {
        "spot": "25_research_summary",
        "market": "_market_summary",
        "fo": "_fo_summary",
    }[scope]
    import re

    assert tooling.summarize_turns(
        d, summary_stage, re.compile("^" + prefix + r"(\d+)_(bull|bear)$")
    )["ok"]
    conviction = 0.05
    assert p.generate_turn(ctx, d, RESEARCHES[scope], 1, runner=fake, bin_path=Path("/fake/codex"))[
        "ok"
    ]
    assert p.completion_error(conn, settings, d, RESEARCHES[scope]) is None
    assert read_checkpoint(d, summary_stage) is None
    if scope == "spot":
        context = spot.ToolContext(
            settings=settings, accounts=[], conn=conn, state=tmp_path, now=lambda: NOW
        )
        bundle = spot.checkpoint_bundle(
            context, ticker="005930", market="KR", date=DAY, kind="trader"
        )
        submit = spot.submit_decision(context, ticker="005930", market="KR", date=DAY, payload={})
    elif scope == "market":
        context = market.MarketToolContext(
            settings=settings, conn=conn, state=tmp_path, now=lambda: NOW
        )
        bundle = market.market_bundle(context, market="KR", date=DAY, kind="strategist")
        submit = market.submit_market_view(context, market="KR", date=DAY, payload={})
    else:
        context = fo.FoToolContext(
            settings=settings, conn=conn, state=tmp_path, now=lambda: NOW, fo=SimpleNamespace()
        )
        bundle = fo.fo_bundle(context, date=DAY, kind="trader")
        submit = fo.submit_fo_decision(context, date=DAY, payload={})
    assert not bundle["ok"] and summary_stage in bundle["error"]
    assert not submit["ok"] and summary_stage in submit["error"]
    if scope == "spot":
        summarized = spot.checkpoint_summarize(context, ticker="005930", market="KR", date=DAY)
    elif scope == "market":
        summarized = market.market_summarize(context, market="KR", date=DAY)
    else:
        summarized = fo.fo_summarize(context, date=DAY)
    assert summarized["ok"], summarized
    assert read_checkpoint(d, summary_stage)["data"]["final"]["bear"]["conviction"] == 0.05
    existing = checkpoint_payload({"sentinel": "previously accepted decision"})
    write_checkpoint(d, r.completed_stage, existing)
    assert p.generate_turn(ctx, d, r, 1, runner=fake, bin_path=Path("/fake/codex"))["ok"]
    assert read_checkpoint(d, r.completed_stage) == existing


@pytest.mark.parametrize("scope", ["spot", "market", "fo"])
def test_general_turn_write_invalidates_summary_and_preserves_saved_decision(conn, tmp_path, scope):
    from trader.features.fo import tools as fo
    from trader.features.regime import tools as market
    from trader.features.spot import tools as spot

    settings = SimpleNamespace(llm=LlmSettings(), debate_rounds=1)
    r = RESEARCHES[scope]
    d = (
        run_dir(DAY, "KR", "005930", tmp_path)
        if scope == "spot"
        else market_dir(DAY, "KR", tmp_path, create=True)
    )
    inputs = {
        "meta": {"market": "KR", "ticker": "005930", "proxy": "005930", "asof": NOW.isoformat()}
    }
    write_checkpoint(d, r.input_stage, inputs if scope == "spot" else checkpoint_payload(inputs))
    for role in ("technical", "sentiment"):
        write_checkpoint(
            d,
            r.report_prefix + role if scope != "market" else r.report_prefix,
            checkpoint_payload(ok_report(role=role)),
        )
    write_checkpoint(d, r.summary_stage, checkpoint_payload({"sentinel": "old summary"}))
    existing = checkpoint_payload({"sentinel": "existing accepted decision"})
    write_checkpoint(d, r.completed_stage, existing)
    turn = ok_turn("bull", 0)
    if scope == "spot":
        ctx = spot.ToolContext(settings=settings, accounts=[], conn=conn, state=tmp_path)
        out = spot.checkpoint_write(
            ctx,
            ticker="005930",
            market="KR",
            date=DAY,
            stage=r.turn_prefix + "0_bull",
            payload=turn,
        )
    elif scope == "market":
        ctx = market.MarketToolContext(settings=settings, conn=conn, state=tmp_path)
        out = market.market_write(
            ctx, market="KR", date=DAY, stage=r.turn_prefix + "0_bull", payload=turn
        )
    else:
        ctx = fo.FoToolContext(settings=settings, conn=conn, state=tmp_path)
        out = fo.fo_write(ctx, date=DAY, stage=r.turn_prefix + "0_bull", payload=turn)
    assert out["ok"], out
    assert read_checkpoint(d, r.summary_stage) is None
    assert read_checkpoint(d, r.completed_stage) == existing
