"""spot FAKE_CLAUDE=1 세션(`run_fake_analyze`)과 에이전트 md 로드 테스트.

옛 `tests/orchestrator/test_session.py` 의 FAKE 4건과 `tests/agents/test_registry.py` 의 md 2건을
이식했다. 세션은 커널 `run_session` 을 통해 돌리므로 runs·agent_calls 기록도 같이 본다."""

from decimal import Decimal

import pytest

from tests.features.spot.conftest import T0, D, make_account
from tests.features.spot.helpers import ok_report
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.spot import ledger, resume
from trader.features.spot.feature import FEATURE
from trader.features.spot.session_kinds import AGENT_NAMES, ANALYZE, RESUME
from trader.kernel.config import Secrets
from trader.kernel.lane.types import LaneContext
from trader.kernel.notify import NullNotifier
from trader.kernel.session import run_store
from trader.kernel.session.agents import by_name, load_agent
from trader.kernel.session.run_store import list_checkpoints, read_checkpoint, run_dir
from trader.kernel.session.runner import SessionSpec, run_session

COMMON = ["제공된 데이터만", "수치", "data_gaps", "UNKNOWN", "검증", "한국어"]


@pytest.fixture
def lane_ctx(project, conn, settings):
    acc = AccountsPortImpl([make_account("paper-main", "paper", "auto", ["KR", "US"])], Secrets())
    return LaneContext(
        settings=settings,
        conn=conn,
        state=project / "state",
        now=lambda: T0,
        dry_run=True,
        notifier=NullNotifier(),
        ports={"accounts": acc},
    )


def _spec(**over) -> SessionSpec:
    base = {
        "market": "KR",
        "date": D,
        "tickers": [{"ticker": "005930", "exchange": None}],
        "dry_run": True,
    }
    base.update(over)
    return SessionSpec(**base)


def _inputs(ticker="005930", market="KR"):
    d = run_dir(D, market, ticker)
    run_store.write_checkpoint(
        d,
        "00_inputs",
        {
            "meta": {
                "ticker": ticker,
                "market": market,
                "name": "삼성전자",
                "asof": T0.isoformat(),
            },
            "technical": {"quote": {"data": {"price": "71500"}, "received_at": "", "error": None}},
            "fundamental": {},
            "news_macro": {},
            "sentiment": {},
            "errors": [],
        },
    )
    return d


def _run(lane_ctx, **kw):
    return run_session(
        _spec(), ANALYZE, lane_ctx, agents=by_name(FEATURE.agents), checkpoint_names=(), **kw
    )


def test_fake_session_writes_all_stages(lane_ctx, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    d = _inputs()
    res = _run(
        lane_ctx,
        runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess 호출 금지")),
    )
    assert res.ok and res.fake
    stages = list_checkpoints(d)
    for s in (
        "10_analyst_technical",
        "10_analyst_fundamental",
        "10_analyst_news_macro",
        "10_analyst_sentiment",
        "20_debate_r0_bull",
        "20_debate_r0_bear",
        "20_debate_r1_bull",
        "20_debate_r1_bear",
        "25_research_summary",
        "30_decision",
        "40_risk_check_paper-main",
    ):
        assert s in stages, s
    dec = read_checkpoint(d, "30_decision")
    assert dec["data"]["action"] == "HOLD" and dec["call"]["model"] == "fake"
    assert lane_ctx.conn.execute("SELECT status FROM agent_calls").fetchone()["status"] == "ok"


def test_fake_skips_debate_on_analyst_consensus(lane_ctx, monkeypatch):
    """애널리스트 4명이 같은 방향·높은 확신이면 FAKE 도 토론 없이 결정한다 (L17)."""
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    d = _inputs()
    for role in ("technical", "fundamental", "news_macro", "sentiment"):
        payload = ok_report(role=role, signal="BUY", confidence=0.8)
        run_store.write_checkpoint(
            d, f"10_analyst_{role}", {"schema_version": 1, "data": payload, "call": None}
        )
    res = _run(lane_ctx)
    assert res.ok
    stages = list_checkpoints(d)
    assert not any(s.startswith("20_debate_") for s in stages)
    assert read_checkpoint(d, "25_research_summary")["data"]["skipped_debate"] is True
    assert "30_decision" in stages
    assert res.stages_written[:1] == ["25_research_summary"]


def test_fake_skips_existing(lane_ctx, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    d = _inputs()
    _run(lane_ctx)
    first = (d / "10_analyst_technical.json").stat().st_mtime_ns
    (d / "20_debate_r1_bear.json").unlink()
    (d / "30_decision.json").unlink()
    res = _run(lane_ctx)
    assert res.ok and res.stages_written == [
        "20_debate_r1_bear",
        "25_research_summary",
        "30_decision",
    ]
    assert (d / "10_analyst_technical.json").stat().st_mtime_ns == first


def test_fake_session_without_inputs_fails(lane_ctx, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    res = _run(lane_ctx)
    assert res.ok is False and "00_inputs" in res.error


def test_analyze_kind_declaration():
    assert ANALYZE.kind == "analyze" and ANALYZE.agents == AGENT_NAMES
    assert ANALYZE.prompt_section == "" and ANALYZE.timeout_key == "analyze"
    assert ANALYZE.runs_kind == "analyze" and ANALYZE.extra_cap_key is None
    assert ANALYZE.throttle == "hard" and ANALYZE.stdin_extra is None
    assert [s.__name__ for s in ANALYZE.schemas] == ["AnalystReport", "DebateTurn", "Decision"]
    assert set(by_name(FEATURE.agents)) == {*AGENT_NAMES, "orchestrator"}


def test_all_agents_load_with_common_rules():
    for name, defn in by_name(FEATURE.agents).items():
        if name == "orchestrator":
            continue
        a = load_agent(defn)
        assert a.name == name and a.description and a.model in ("haiku", "sonnet", "opus")
        assert not a.prompt.startswith("---")
        for phrase in COMMON:
            assert phrase in a.prompt, (name, phrase)
        assert "# 출력 규칙" in a.prompt and '"type":"object"' in a.prompt  # 스키마 전문 포함


def test_role_specific_rules():
    ag = by_name(FEATURE.agents)
    assert "악마의 변호인" in load_agent(ag["researcher-bear"]).prompt
    t = load_agent(ag["trader"]).prompt
    assert "HOLD" in t and "round0" in t and "30bp" in t and "LIMIT" in t
    assert "부정" in load_agent(ag["analyst-news-macro"]).prompt


def test_orchestrator_has_no_other_lane_modes():
    p = load_agent(by_name(FEATURE.agents)["orchestrator"]).prompt
    for mode in ("reflect", "market", "fo", "fo_resume", "discover", "screen"):
        assert f"## {mode} 모드" not in p


def test_fake_resume_session_writes_31_decision(lane_ctx, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    d = _inputs()
    assert _run(lane_ctx).ok  # 아침 분석(30_decision)
    tid = ledger.insert_trigger(
        lane_ctx.conn,
        market="KR",
        ticker="005930",
        kind="take_profit",
        detail={"level": "80000"},
        price=Decimal("80000"),
        decision_id=ledger.decision_id(lane_ctx.conn, D, "KR", "005930"),
        account_id="paper-main",
        created_at=T0.isoformat(),
    )

    def go():
        return resume.reevaluate(
            lane_ctx,
            market="KR",
            date=D,
            ticker="005930",
            exchange=None,
            trigger_id=tid,
            trigger={"kind": "take_profit", "price": "80000", "detail": {}},
        )

    res = go()
    assert res.ok and "31_decision_r1" in list_checkpoints(d)
    row = lane_ctx.conn.execute("SELECT handled_at, session_id FROM triggers").fetchone()
    assert row["handled_at"] and row["session_id"] == "fake"
    assert resume.handled_marker(res) == "fake"
    res2 = go()
    assert res2.ok and "31_decision_r2" in list_checkpoints(d)


def test_resume_kind_declaration():
    assert RESUME.kind == "resume" and RESUME.agents == AGENT_NAMES
    assert RESUME.prompt_section == "resume" and RESUME.timeout_key == "resume"
    assert RESUME.runs_kind == "resume" and RESUME.extra_cap_key is None
    assert RESUME.throttle == "soft" and RESUME.retry_guard is False
    spec = _spec(kind="resume", extra={"trigger": {"kind": "news"}})
    assert RESUME.stdin_extra(spec) == {"trigger": {"kind": "news"}}
    assert FEATURE.session_kinds == (ANALYZE, RESUME)
    p = load_agent(by_name(FEATURE.agents)["orchestrator"]).prompt
    assert "## resume 모드" in p and "31_decision_r<n>" in p


def test_fake_session_with_codex_enabled_never_claims_or_launches_codex(lane_ctx, monkeypatch):
    from trader.kernel.session import codex

    monkeypatch.setenv("FAKE_CLAUDE", "1")
    lane_ctx.settings.llm.codex_debate.enabled = True
    monkeypatch.setattr(codex, "run_turn", lambda *a, **kw: pytest.fail("FAKE launched Codex"))
    _inputs()
    res = _run(lane_ctx)
    assert res.ok and res.fake
    assert lane_ctx.conn.execute("SELECT COUNT(*) FROM research_calls").fetchone()[0] == 0
