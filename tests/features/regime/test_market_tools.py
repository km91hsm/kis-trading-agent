"""시장 세션 도구(Phase 18): market bundle/write/summarize·submit-market-view.

옛 `tests/orchestrator/test_market_tools.py` 4건 이식. 바뀐 것은 컨텍스트(`MarketToolContext`)·
FAKE 진입점(`write_fake_market`)·import 경로뿐이다."""

from datetime import UTC, datetime

import pytest

from tests.features.spot.helpers import ok_report, ok_turn
from trader.features.regime import fake, tools
from trader.kernel.ledger import control
from trader.kernel.session.run_store import market_dir, read_checkpoint, write_checkpoint

T0 = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)  # 2026-10-09 15:00 KST
D = T0.date()


@pytest.fixture
def ctx(project, conn, settings):
    return tools.MarketToolContext(settings=settings, conn=conn, now=lambda: T0)


def _inputs(market="KR", state="bear"):
    d = market_dir(D, market, None, create=True)
    write_checkpoint(
        d,
        "_market_inputs",
        {
            "data": {
                "market": market,
                "date": D.isoformat(),
                "proxy": "KOSPI200",
                "regime": {"state": state, "count": 2},
                "hedge": {"held": []},
                "sector_weights": [{"sector": "반도체", "weight": 0.4}],
                "macro": {},
                "macro_news": [],
            }
        },
    )
    return d


def _proposal(**over):
    p = {
        "market": "KR",
        "stance": "RISK_OFF",
        "hedge_weight": 0.12,
        "leveraged": False,
        "confidence": 0.7,
        "horizon_days": 20,
        "rationale": "200일선 아래·변동성 확대",
        "invalidation_conditions": ["지수가 200일선을 회복"],
    }
    p.update(over)
    return p


def test_bundle_requires_inputs_then_report(ctx):
    assert tools.market_bundle(ctx, market="KR", date=D, kind="analyst")["ok"] is False
    _inputs()
    b = tools.market_bundle(ctx, market="KR", date=D, kind="analyst")
    assert b["ok"] and b["data"]["scope"] == "market" and b["data"]["regime"]["state"] == "bear"
    r = tools.market_bundle(ctx, market="KR", date=D, kind="researcher", round=0, side="bull")
    assert r["ok"] is False and "_market_analyst" in r["error"]
    assert tools.market_bundle(ctx, market="XX", date=D, kind="analyst")["ok"] is False


def test_write_validates_stage_round_and_side(ctx):
    _inputs()
    rep = ok_report(ticker="KOSPI200", role="news_macro")
    bad_stage = tools.market_write(ctx, market="KR", date=D, stage="30_decision", payload=rep)
    assert bad_stage["ok"] is False
    w = tools.market_write(
        ctx, market="KR", date=D, stage="_market_analyst", payload=rep, agent="analyst-market"
    )
    assert w["ok"] and read_checkpoint(market_dir(D, "KR"), "_market_analyst")["call"]["agent"] == (
        "analyst-market"
    )
    bad = tools.market_write(
        ctx, market="KR", date=D, stage="_market_debate_r0_bull", payload=ok_turn("bear", 0)
    )
    assert bad["ok"] is False and "불일치" in bad["error"]
    too_far = tools.market_write(
        ctx, market="KR", date=D, stage="_market_debate_r5_bull", payload=ok_turn("bull", 5)
    )
    assert too_far["ok"] is False and "debate_rounds" in too_far["error"]
    schema = tools.market_write(
        ctx, market="KR", date=D, stage="_market_analyst", payload={"ticker": "x"}
    )
    assert schema["ok"] is False and "스키마" in schema["error"]


def test_researcher_bundle_round1_needs_both_turns_and_summary_submit_flow(ctx, conn):
    _inputs()
    tools.market_write(
        ctx,
        market="KR",
        date=D,
        stage="_market_analyst",
        payload=ok_report(ticker="KOSPI200", role="news_macro", signal="SELL", score=-0.5),
    )
    assert tools.market_summarize(ctx, market="KR", date=D)["ok"] is False  # 턴 없음
    assert tools.submit_market_view(ctx, market="KR", date=D, payload=_proposal())["ok"] is False
    r0 = tools.market_bundle(ctx, market="KR", date=D, kind="researcher", round=0, side="bull")
    assert r0["ok"] and r0["data"]["opponent_previous"] is None and len(r0["data"]["reports"]) == 1
    for side in ("bull", "bear"):
        tools.market_write(
            ctx, market="KR", date=D, stage=f"_market_debate_r0_{side}", payload=ok_turn(side, 0)
        )
    r1 = tools.market_bundle(ctx, market="KR", date=D, kind="researcher", round=1, side="bear")
    assert r1["ok"] and r1["data"]["opponent_previous"]["side"] == "bull"
    assert tools.market_bundle(ctx, market="KR", date=D, kind="strategist")["ok"] is False
    s = tools.market_summarize(ctx, market="KR", date=D)
    assert s["ok"] and "sycophantic" in s
    st = tools.market_bundle(ctx, market="KR", date=D, kind="strategist")
    assert st["ok"] and st["data"]["hedge"]["max_weight_pct"] == 0.2
    assert st["data"]["sector_weights"][0]["sector"] == "반도체"
    bad_market = tools.submit_market_view(ctx, market="KR", date=D, payload=_proposal(market="US"))
    assert bad_market["ok"] is False
    out = tools.submit_market_view(ctx, market="KR", date=D, payload=_proposal())
    assert out["ok"] and out["hedge_weight"] == 0.12
    view = read_checkpoint(market_dir(D, "KR"), "_market_view")
    assert view["data"]["stance"] == "RISK_OFF" and view["call"]["agent"] == "strategist"
    flag = control.get_flag(conn, "market_view:KR")
    assert flag["date"] == D.isoformat() and flag["hedge_weight"] == 0.12
    assert tools.market_view_today(conn, "KR", D)["stance"] == "RISK_OFF"
    assert tools.market_view_today(conn, "KR", D.replace(day=D.day + 1)) is None  # 어제 것은 안 씀
    assert tools.market_view_today(conn, "US", D) is None


def test_fake_market_session_writes_all_stages(ctx, conn):
    _inputs(state="bear")
    written = fake.write_fake_market(ctx, market="KR", date=D, debate_rounds=1)
    assert written[0] == "_market_analyst" and written[-1] == "_market_view"
    assert control.get_flag(conn, "market_view:KR")["stance"] == "RISK_OFF"
    _inputs("US", state="bull")
    fake.write_fake_market(ctx, market="US", date=D, debate_rounds=1)
    v = control.get_flag(conn, "market_view:US")
    assert v["stance"] == "RISK_ON" and v["hedge_weight"] == 0.0


# --- 교차 토론(2판) ---------------------------------------------------------------


def _issue_turn(side, rnd=0, **over):
    """쟁점 토론 검사를 통과하는 턴(쟁점 `<side>:case`, 근거는 ok_report 의 source_id)."""
    return ok_turn(
        side,
        rnd,
        issues=[
            {
                "issue_id": f"{side}:case",
                "topic": "쟁점",
                "claim": "근거 주장",
                "evidence": ["technical.indicators"],
            }
        ],
        **over,
    )


def _with_report(ctx, *, cross=False):
    """`_market_inputs` + (교차면 v2 정책 고정) + `_market_analyst`."""
    from trader.features.regime.research import RESEARCH
    from trader.kernel.session import research_policy

    d = _inputs()
    if cross:
        ctx.settings.llm.codex_debate.enabled = True
        policy = research_policy.start(ctx.conn, ctx.settings, d, RESEARCH, now=T0)
        assert research_policy.is_cross(policy)
    write_checkpoint(
        d,
        "_market_analyst",
        {"data": ok_report(ticker="KOSPI200", role="news_macro"), "call": None},
    )
    return d


def _save_turn(d, stage, payload, provider):
    write_checkpoint(d, stage, {"data": payload, "call": {"agent": "x", "provider": provider}})


def _write_both_lanes(d):
    for prefix in ("_market_debate_r", "_market_debatex_r"):
        for side in ("bull", "bear"):
            _save_turn(d, f"{prefix}0_{side}", ok_turn(side, 0), "claude")


def test_market_bundle_lane_b_uses_lane_b_previous(ctx, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    d = _with_report(ctx, cross=True)
    _save_turn(d, "_market_debate_r0_bull", _issue_turn("bull", arguments=["판A 강세"]), "claude")
    _save_turn(d, "_market_debate_r0_bear", _issue_turn("bear", arguments=["판A 약세"]), "codex")
    _save_turn(d, "_market_debatex_r0_bull", _issue_turn("bull", arguments=["판B 강세"]), "codex")
    _save_turn(d, "_market_debatex_r0_bear", _issue_turn("bear", arguments=["판B 약세"]), "claude")
    out = tools.market_bundle(
        ctx, market="KR", date=D, kind="researcher", round=1, side="bear", lane="b"
    )
    assert out["ok"] is True, out
    assert out["data"]["lane"] == "b"
    assert out["data"]["opponent_previous"]["arguments"] == ["판B 강세"]
    assert out["data"]["own_previous"]["arguments"] == ["판B 약세"]
    a = tools.market_bundle(ctx, market="KR", date=D, kind="researcher", round=1, side="bull")
    assert a["data"]["lane"] == "a"
    assert a["data"]["opponent_previous"]["arguments"] == ["판A 약세"]


def test_market_bundle_lane_b_rejected_without_cross_policy(ctx, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    _with_report(ctx)
    out = tools.market_bundle(
        ctx, market="KR", date=D, kind="researcher", round=0, side="bull", lane="b"
    )
    assert out == {"ok": False, "error": "이 연구에는 2판이 없습니다"}
    bad = tools.market_bundle(
        ctx, market="KR", date=D, kind="researcher", round=0, side="bull", lane="c"
    )
    assert bad == {"ok": False, "error": "--lane 은 a|b 입니다"}


def test_market_write_rejects_claude_in_codex_slot_lane_b(ctx, monkeypatch):
    """v2 위조 회귀: 판 b Codex 강세 칸을 Claude 체크포인트로 쓰면 쓰기 단계에서 거부."""
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    d = _with_report(ctx, cross=True)
    out = tools.market_write(
        ctx, market="KR", date=D, stage="_market_debatex_r0_bull", payload=_issue_turn("bull")
    )
    assert out["ok"] is False
    assert out["error"].startswith("Codex bull는 전용 도구로만 기록할 수 있습니다")
    assert read_checkpoint(d, "_market_debatex_r0_bull") is None


def test_market_write_accepts_claude_lane_b_bear(ctx, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    d = _with_report(ctx, cross=True)
    out = tools.market_write(
        ctx,
        market="KR",
        date=D,
        stage="_market_debatex_r0_bear",
        payload=_issue_turn("bear"),
        agent="market-researcher-bear",
    )
    assert out == {
        "ok": True,
        "path": str(d / "_market_debatex_r0_bear.json"),
        "stage": "_market_debatex_r0_bear",
    }
    saved = read_checkpoint(d, "_market_debatex_r0_bear")
    assert saved["call"]["provider"] == "claude" and saved["data"]["side"] == "bear"


def test_market_write_lane_b_rejected_without_cross_policy(ctx, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    d = _with_report(ctx)
    out = tools.market_write(
        ctx, market="KR", date=D, stage="_market_debatex_r0_bear", payload=ok_turn("bear", 0)
    )
    assert out == {"ok": False, "error": "이 연구에는 2판이 없습니다"}
    assert read_checkpoint(d, "_market_debatex_r0_bear") is None


def test_market_codex_turn_side_passthrough(ctx, monkeypatch):
    from trader.kernel.session import research_policy

    _with_report(ctx)
    seen = {}

    def fake_generate(ctx_, d, scope, round_no, *, side="bear", **kw):
        seen.update(round=round_no, side=side, scope=scope.scope)
        return {"ok": True, "side": side}

    monkeypatch.setattr(research_policy, "generate_turn", fake_generate)
    out = tools.codex_turn(ctx, market="KR", date=D, round=0, side="bull")
    assert out == {"ok": True, "side": "bull"}
    assert seen == {"round": 0, "side": "bull", "scope": "market"}
    tools.codex_turn(ctx, market="KR", date=D, round=1)
    assert seen == {"round": 1, "side": "bear", "scope": "market"}
    bad = tools.codex_turn(ctx, market="KR", date=D, round=0, side="x")
    assert bad == {"ok": False, "error": "--side 는 bull|bear 입니다"}


def test_market_cli_lane_and_side_options(monkeypatch):
    from typer.testing import CliRunner

    from trader.features.regime import tool_cli

    seen = {}

    def fake_run(fn, **kw):
        seen[fn.__name__] = kw

    monkeypatch.setattr(tool_cli, "_run", fake_run)
    runner = CliRunner()
    base = ["--market", "KR", "--date", D.isoformat()]
    args = ["bundle", *base, "--kind", "researcher", "--round", "0", "--side", "bull"]
    assert runner.invoke(tool_cli.market_app, args).exit_code == 0
    assert seen["market_bundle"]["lane"] == "a"
    assert runner.invoke(tool_cli.market_app, [*args, "--lane", "b"]).exit_code == 0
    assert seen["market_bundle"]["lane"] == "b"
    assert runner.invoke(tool_cli.market_app, ["codex-turn", *base, "--round", "0"]).exit_code == 0
    assert seen["codex_turn"]["side"] == "bear"
    side_args = ["codex-turn", *base, "--round", "0", "--side", "bull"]
    res = runner.invoke(tool_cli.market_app, side_args)
    assert res.exit_code == 0
    assert seen["codex_turn"]["side"] == "bull"


def test_market_summarize_merges_two_lanes(ctx, monkeypatch):
    """두 판 정책(FAKE·Codex 켜짐 설정 — 증명 검사 없음)에서 요약이 두 판을 합친다."""
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    ctx.settings.llm.codex_debate.enabled = True
    d = _with_report(ctx)
    _write_both_lanes(d)
    out = tools.market_summarize(ctx, market="KR", date=D)
    assert out["ok"] is True, out
    assert out["lanes"] == ["a", "b"]
    data = read_checkpoint(d, "_market_summary")["data"]
    assert data["lanes"] == ["a", "b"]
    assert set(data["by_lane"]) == {"a", "b"}


def test_market_summarize_rejects_lane_b_without_lane_b_policy(ctx, monkeypatch):
    """심층 방어: 판 b 가 없는 정책인데 판 b 파일이 있으면 요약하지 않는다."""
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    d = _with_report(ctx)
    _write_both_lanes(d)
    out = tools.market_summarize(ctx, market="KR", date=D)
    assert out == {"ok": False, "error": "이 연구에는 2판이 없습니다"}
    assert read_checkpoint(d, "_market_summary") is None


def test_market_summarize_cross_policy_incomplete_lane_b_rejected(ctx, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    d = _with_report(ctx, cross=True)
    for n in (0, 1):
        for side in ("bull", "bear"):
            _save_turn(d, f"_market_debate_r{n}_{side}", ok_turn(side, n), "claude")
    out = tools.market_summarize(ctx, market="KR", date=D)
    assert out["ok"] is False and "2판 r0_bull" in out["error"]
    assert read_checkpoint(d, "_market_summary") is None


def test_market_fake_cross_debate_writes_both_lanes(ctx, conn, monkeypatch):
    """Codex 켜짐 설정 + FAKE: 라운드마다 두 판 4턴과 by_lane 요약(실제 Codex 는 띄우지 않는다)."""
    from trader.kernel.session import codex

    monkeypatch.setenv("FAKE_CLAUDE", "1")
    ctx.settings.llm.codex_debate.enabled = True
    monkeypatch.setattr(codex, "run_turn", lambda *a, **kw: pytest.fail("FAKE launched Codex"))
    d = _inputs()
    written = fake.write_fake_market(ctx, market="KR", date=D, debate_rounds=1)
    for n in (0, 1):
        for s in ("bull", "bear"):
            assert f"_market_debate_r{n}_{s}" in written
            assert f"_market_debatex_r{n}_{s}" in written
    assert read_checkpoint(d, "_market_debatex_r0_bull")["call"]["agent"] == (
        "market-researcher-bull"
    )
    summ = read_checkpoint(d, "_market_summary")["data"]
    assert set(summ["by_lane"]) == {"a", "b"} and summ["lanes"] == ["a", "b"]
    assert written[-1] == "_market_view"


def test_market_fake_without_codex_writes_single_lane(ctx, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    ctx.settings.llm.codex_debate.enabled = False
    d = _inputs()
    written = fake.write_fake_market(ctx, market="KR", date=D, debate_rounds=1)
    assert "_market_debate_r0_bull" in written and "_market_debate_r1_bear" in written
    assert not any(s.startswith("_market_debatex_") for s in written)
    assert "by_lane" not in read_checkpoint(d, "_market_summary")["data"]
