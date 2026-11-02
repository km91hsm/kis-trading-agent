"""spot 도구(status·checkpoint list/read/write/bundle/summarize·requote·CLI) 테스트.

옛 `tests/orchestrator/test_tools.py` 에서 이식. submit-decision·resume 은 Task 11 몫이다."""

import json
from datetime import date
from decimal import Decimal

import pytest

from tests.features.spot.conftest import T0, D, make_account
from tests.features.spot.helpers import ok_decision, ok_report, ok_turn
from trader.features.accounts import api as accounts_api
from trader.features.spot import tools
from trader.kernel.execution import ledger as exec_ledger
from trader.kernel.session import run_store
from trader.kernel.session.run_store import read_checkpoint, run_dir


@pytest.fixture
def ctx(project, conn, settings):
    accounts = [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("real-main", "real", "off", markets=["KR"]),
    ]
    return tools.ToolContext(
        settings=settings,
        accounts=accounts,
        conn=conn,
        state=project / "state",
        now=lambda: T0,
        policy=accounts_api.effective_mode,
        paused=accounts_api.is_paused,
    )


def _inputs(project, ticker="005930", market="KR"):
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


def test_checkpoint_write_ok_and_list(ctx, project):
    _inputs(project)
    r = tools.checkpoint_write(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="10_analyst_technical",
        payload=ok_report(),
        agent="analyst-technical",
    )
    assert r["ok"] is True and r["path"].endswith("10_analyst_technical.json")
    saved = read_checkpoint(run_dir(D, "KR", "005930"), "10_analyst_technical")
    assert saved["schema_version"] == 1 and saved["data"]["signal"] == "BUY"
    assert saved["call"]["agent"] == "analyst-technical"
    r = tools.checkpoint_list(ctx, ticker="005930", market="KR", date=D)
    assert r == {"ok": True, "stages": ["00_inputs", "10_analyst_technical"]}


def test_checkpoint_write_rejects_invalid(ctx, project):
    _inputs(project)
    r = tools.checkpoint_write(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="10_analyst_technical",
        payload=ok_report(score=2.0),
        agent="analyst-technical",
    )
    assert r["ok"] is False and "score" in r["error"]
    assert read_checkpoint(run_dir(D, "KR", "005930"), "10_analyst_technical") is None
    r = tools.checkpoint_write(
        ctx, ticker="005930", market="KR", date=D, stage="99_unknown", payload={}, agent="x"
    )
    assert r["ok"] is False and "알 수 없는" in r["error"]


def test_checkpoint_write_rejects_decision_stage(ctx, project):
    """LLM 이 30_decision 을 직접 써서 submit-decision 전제조건을 우회할 수 없다 (리뷰 C1)."""
    _inputs(project)
    r = tools.checkpoint_write(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="30_decision",
        payload=ok_decision(),
        agent="trader",
    )
    assert r["ok"] is False and "submit-decision" in r["error"]
    assert read_checkpoint(run_dir(D, "KR", "005930"), "30_decision") is None
    r = tools.checkpoint_write(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="25_research_summary",
        payload={},
        agent="x",
    )
    assert r["ok"] is False


def test_checkpoint_write_rejects_name_payload_mismatch(ctx, project):
    _inputs(project)
    w = lambda stage, payload: tools.checkpoint_write(  # noqa: E731
        ctx, ticker="005930", market="KR", date=D, stage=stage, payload=payload, agent="a"
    )
    assert w("10_analyst_technical", ok_report(role="sentiment"))["ok"] is False
    assert w("10_analyst_technical", ok_report(ticker="000660"))["ok"] is False
    assert w("10_analyst_bogus", ok_report(role="technical"))["ok"] is False
    assert w("20_debate_r0_bull", ok_turn("bear"))["ok"] is False
    assert w("20_debate_r1_bull", ok_turn("bull", round=0))["ok"] is False
    assert w("20_debate_r2_bull", ok_turn("bull", round=2))["ok"] is False  # debate_rounds=1
    assert list(run_dir(D, "KR", "005930").glob("10_analyst_*")) == []
    # StageRule 전제조건(애널리스트 2개)을 채우면 r1 토론 턴은 통과한다
    for role in ("technical", "fundamental"):
        assert w(f"10_analyst_{role}", ok_report(role=role))["ok"] is True
    assert w("20_debate_r1_bull", ok_turn("bull", round=1))["ok"] is True


def test_tools_reject_path_traversal(ctx, project):
    """ticker·market·stage 가 경로로 쓰이므로 state/ 밖·토큰 캐시를 가리킬 수 없다 (리뷰 C2)."""
    _inputs(project)
    (project / "state" / "kis_token_real.json").write_text(
        '{"access_token": "SECRET"}', encoding="utf-8"
    )
    r = tools.checkpoint_read(
        ctx, ticker="005930", market="KR", date=D, stage="../../../../kis_token_real"
    )
    assert r["ok"] is False and "SECRET" not in json.dumps(r)
    assert tools.checkpoint_list(ctx, ticker="../../..", market="KR", date=D)["ok"] is False
    assert tools.checkpoint_list(ctx, ticker="005930", market="../etc", date=D)["ok"] is False
    assert tools.checkpoint_list(ctx, ticker="005930", market="XX", date=D)["ok"] is False
    assert not (project / "state" / "etc").exists()
    assert tools.checkpoint_list(ctx, ticker="BRK.B", market="US", date=D)["ok"] is True


def test_checkpoint_write_unescapes_html_entities(ctx, project):
    """Claude Code 가 서브에이전트 출력을 이스케이프하므로 &lt; 등을 되돌려 저장한다 (리뷰 I7)."""
    _inputs(project)
    tools.checkpoint_write(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="10_analyst_technical",
        payload=ok_report(bullets=["종가 &lt; SMA20 &amp; MACD&gt;0"]),
        agent="a",
    )
    saved = read_checkpoint(run_dir(D, "KR", "005930"), "10_analyst_technical")
    assert saved["data"]["bullets"] == ["종가 < SMA20 & MACD>0"]


def test_status_does_not_create_placeholder_dir(ctx, project):
    _inputs(project)
    tools.status(ctx, market="KR", date=D)
    assert not (project / "state" / "runs" / "2026-10-09" / "KR" / "_").exists()
    r = tools.status(ctx, market="US", date=D)
    assert r["ok"] and r["tickers"] == {}


def test_checkpoint_read_section(ctx, project):
    _inputs(project)
    r = tools.checkpoint_read(
        ctx, ticker="005930", market="KR", date=D, stage="00_inputs", section="technical"
    )
    assert r["ok"] and r["data"]["quote"]["data"]["price"] == "71500"
    r = tools.checkpoint_read(ctx, ticker="005930", market="KR", date=D, stage="30_decision")
    assert r["ok"] is False
    r = tools.checkpoint_read(
        ctx, ticker="005930", market="KR", date=D, stage="00_inputs", section="trader_context"
    )
    assert (
        r["ok"]
        and r["data"]["costs_bp"] == 30
        and r["data"]["portfolio"]["paper-main"]["positions"] == []
    )
    assert r["data"]["risk_limits"]["max_position_pct"] == 0.25


def _big_inputs(project, ticker="005930", market="KR"):
    """실기 크기의 00_inputs: 일봉 260·분봉 120·호가 10단·뉴스 20+10·커뮤니티 50."""
    d = run_dir(D, market, ticker)
    candle = {
        "date": "2026-10-08",
        "open": "1",
        "high": "2",
        "low": "1",
        "close": "1.5",
        "volume": 1000,
        "time": None,
    }
    news = [
        {
            "title": "제목 " * 10,
            "summary": "요약 " * 100,
            "source": "s",
            "published_at": "2026-10-08T00:00:00+00:00",
            "url": f"https://x/{i}",
            "received_at": "",
        }
        for i in range(20)
    ]
    posts = [
        {
            "title": "글 " * 20,
            "body": "본문 " * 200,
            "author": "a",
            "posted_at": "2026-10-08T00:00:00+00:00",
            "url": f"https://c/{i}",
            "received_at": "",
            "source": "clien",
        }
        for i in range(50)
    ]
    sec = lambda data: {"data": data, "received_at": "", "error": None}  # noqa: E731
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
            "technical": {
                "quote": sec({"price": "71500"}),
                "daily": sec([candle] * 260),
                "minute": sec([{**candle, "time": "153000"}] * 120),
                "orderbook": sec({"asks": [["1", 1]] * 10, "bids": [["1", 1]] * 10}),
                "indicators": sec({"sma_5": 1.0}),
            },
            "fundamental": {"ratios": sec({"ROE": 0.1})},
            "news_macro": {
                "news": sec({"stock": news, "macro": news[:10], "sources": {"google_rss": "ok"}}),
                "macro": {"vix": sec({"value": 15.0, "date": "2026-10-08"})},
            },
            "sentiment": {
                "investor_flow": sec(
                    [
                        {
                            "date": "2026-10-08",
                            "foreign_net": 1,
                            "institution_net": 1,
                            "individual_net": 1,
                        }
                    ]
                    * 20
                ),
                "community": sec({"posts": posts, "sources": {"clien": "ok"}}),
            },
            "errors": [],
        },
    )


def test_checkpoint_read_section_is_compact(ctx, project):
    """서브에이전트에 넘길 절은 Claude Code Bash 출력 한도(약 30KB)보다 훨씬 작아야 한다."""
    _big_inputs(project)
    for section in ("technical", "fundamental", "news_macro", "sentiment"):
        r = tools.checkpoint_read(
            ctx, ticker="005930", market="KR", date=D, stage="00_inputs", section=section
        )
        assert r["ok"], section
        assert len(json.dumps(r["data"], ensure_ascii=False)) < 12_000, section
    t = tools.checkpoint_read(
        ctx, ticker="005930", market="KR", date=D, stage="00_inputs", section="technical"
    )["data"]
    assert len(t["daily"]["data"]) == 30 and len(t["minute"]["data"]) == 20
    assert len(t["orderbook"]["data"]["asks"]) == 5 and t["indicators"]["data"]["sma_5"] == 1.0
    assert t["daily"]["note"].startswith("최근 30")
    n = tools.checkpoint_read(
        ctx, ticker="005930", market="KR", date=D, stage="00_inputs", section="news_macro"
    )["data"]
    assert len(n["news"]["data"]["stock"]) == 15 and len(n["news"]["data"]["macro"]) == 8
    assert all(len(i["summary"]) <= 120 and "url" not in i for i in n["news"]["data"]["stock"])
    s = tools.checkpoint_read(
        ctx, ticker="005930", market="KR", date=D, stage="00_inputs", section="sentiment"
    )["data"]
    assert len(s["community"]["data"]["posts"]) == 20
    assert all(len(p["body"]) <= 200 for p in s["community"]["data"]["posts"])
    # 절 없이 전체를 읽으면 압축하지 않는다 (파일 내용 그대로)
    full = tools.checkpoint_read(ctx, ticker="005930", market="KR", date=D, stage="00_inputs")[
        "data"
    ]
    assert len(full["technical"]["daily"]["data"]) == 260


def _realistic_reports_and_turns(ctx, project):
    """실기 크기(리포트 ≈3.6KB, 턴 ≈2KB)로 체크포인트를 채운다."""
    _big_inputs(project)
    for role in ("technical", "fundamental", "news_macro", "sentiment"):
        payload = ok_report(  # 실기 리포트 ≈ 3.6KB
            role=role,
            summary="요약 " * 60,
            bullets=["근거 " * 20] * 6,
            key_risks=["위험 " * 15] * 3,
            counter_evidence=["반대 " * 15] * 2,
            data_gaps=["공백 " * 10] * 2,
            evidence=[{"claim": "주장 " * 15, "source_id": f"{role}.x"}] * 5,
        )
        tools.checkpoint_write(
            ctx,
            ticker="005930",
            market="KR",
            date=D,
            stage=f"10_analyst_{role}",
            payload=payload,
            agent="a",
        )
    for rnd in (0, 1):
        for side in ("bull", "bear"):
            payload = ok_turn(  # 실기 턴 ≈ 2KB
                side,
                round=rnd,
                arguments=["논거 " * 30] * 5,
                rebuttals=["반박 " * 30] * 5 if rnd else [],
                what_would_change_my_mind=["조건 " * 10] * 3,
            )
            tools.checkpoint_write(
                ctx,
                ticker="005930",
                market="KR",
                date=D,
                stage=f"20_debate_r{rnd}_{side}",
                payload=payload,
                agent="r",
            )
    tools.checkpoint_summarize(ctx, ticker="005930", market="KR", date=D)


def test_bundle_researcher_round0_has_reports_no_opponent(ctx, project):
    """파이썬이 입력을 묶어 주면 오케스트레이터가 요약할 여지가 없다 (실기 3차 반영)."""
    _realistic_reports_and_turns(ctx, project)
    r = tools.checkpoint_bundle(
        ctx, ticker="005930", market="KR", date=D, kind="researcher", round=0, side="bull"
    )
    assert r["ok"]
    b = r["data"]
    assert b["round"] == 0 and b["side"] == "bull" and len(b["reports"]) == 4
    assert b["own_previous"] is None and b["opponent_previous"] is None
    assert b["reports"][0]["role"] in ("technical", "fundamental", "news_macro", "sentiment")
    assert len(json.dumps(b, ensure_ascii=False).encode("utf-8")) < 24_000


def test_bundle_researcher_round1_has_both_previous_turns(ctx, project):
    _realistic_reports_and_turns(ctx, project)
    b = tools.checkpoint_bundle(
        ctx, ticker="005930", market="KR", date=D, kind="researcher", round=1, side="bear"
    )["data"]
    assert b["own_previous"]["side"] == "bear" and b["own_previous"]["round"] == 0
    assert b["opponent_previous"]["side"] == "bull" and b["opponent_previous"]["round"] == 0
    assert len(b["reports"]) == 4
    assert len(json.dumps(b, ensure_ascii=False).encode("utf-8")) < 24_000


def test_bundle_trader_has_reports_summary_context_within_limit(ctx, project):
    _realistic_reports_and_turns(ctx, project)
    r = tools.checkpoint_bundle(ctx, ticker="005930", market="KR", date=D, kind="trader")
    assert r["ok"]
    b = r["data"]
    assert len(b["reports"]) == 4 and b["research_summary"]["preferred"] in ("round0", "final")
    assert "round0" in b["research_summary"] and "final" in b["research_summary"]
    assert (
        b["context"]["costs_bp"] == 30
        and b["context"]["portfolio"]["paper-main"]["positions"] == []
    )
    assert len(json.dumps(b, ensure_ascii=False).encode("utf-8")) < 24_000
    assert "market_view" not in b["context"]  # 시장 세션 제안은 이연(README 알려진 차이)
    assert "note" not in b  # 실기 크기에서는 손실 축약 없이 들어가야 한다
    assert "evidence" not in b["reports"][0]  # 트레이더 묶음은 evidence 를 뺀다
    assert set(b["research_summary"]["round0"]["bull"]) < set(
        b["research_summary"]["final"]["bull"]
    )


def test_bundle_requires_prerequisites(ctx, project):
    _inputs(project)
    assert (
        tools.checkpoint_bundle(
            ctx, ticker="005930", market="KR", date=D, kind="researcher", round=0, side="bull"
        )["ok"]
        is False
    )
    assert (
        tools.checkpoint_bundle(ctx, ticker="005930", market="KR", date=D, kind="trader")["ok"]
        is False
    )
    assert (
        tools.checkpoint_bundle(ctx, ticker="005930", market="KR", date=D, kind="nope")["ok"]
        is False
    )


def test_summarize_requires_both_sides(ctx, project):
    _inputs(project)
    r = tools.checkpoint_summarize(ctx, ticker="005930", market="KR", date=D)
    assert r["ok"] is False


def test_status_lists_tickers_and_sessions(ctx, project):
    _inputs(project)
    _inputs(project, ticker="000660")
    r = tools.status(ctx, market="KR", date=D)
    assert r["ok"] and set(r["tickers"]) == {"005930", "000660"}
    assert r["tickers"]["005930"] == ["00_inputs"]
    assert r["sessions_today"] == 0 and r["cap"] == ctx.settings.llm.daily_session_cap


def test_cli_tool_outputs_json_only(project, monkeypatch):
    from typer.testing import CliRunner

    from trader.kernel.cli.main import app

    _inputs(project)
    r = CliRunner().invoke(
        app,
        [
            "tool",
            "checkpoint",
            "list",
            "--ticker",
            "005930",
            "--market",
            "KR",
            "--date",
            "2026-10-09",
        ],
    )
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout) == {"ok": True, "stages": ["00_inputs"]}
    r = CliRunner().invoke(
        app,
        [
            "tool",
            "checkpoint",
            "write",
            "--ticker",
            "005930",
            "--market",
            "KR",
            "--date",
            "2026-10-09",
            "--stage",
            "10_analyst_technical",
            "--agent",
            "analyst-technical",
            "--json",
            json.dumps(ok_report()),
        ],
    )
    assert r.exit_code == 0 and json.loads(r.stdout)["ok"] is True


def test_cli_tool_error_is_json_exit1(project):
    """전제조건 위반은 JSON 1줄 + exit 1. (옛 테스트는 submit-decision)"""
    from typer.testing import CliRunner

    from trader.kernel.cli.main import app

    r = CliRunner().invoke(
        app,
        [
            "tool",
            "checkpoint",
            "write",
            "--ticker",
            "005930",
            "--market",
            "KR",
            "--date",
            "2026-10-09",
            "--stage",
            "20_debate_r0_bull",
            "--json",
            json.dumps(ok_turn("bull")),
        ],
    )
    assert r.exit_code == 1
    out = json.loads(r.stdout)
    assert out["ok"] is False and "전제조건" in out["error"]


def test_trader_bundle_includes_portfolio_per_account(ctx, project):
    _inputs(project)
    for role in ("technical", "fundamental"):
        tools.checkpoint_write(
            ctx,
            ticker="005930",
            market="KR",
            date=D,
            stage=f"10_analyst_{role}",
            payload=ok_report(role=role),
            agent="a",
        )
    for side in ("bull", "bear"):
        tools.checkpoint_write(
            ctx,
            ticker="005930",
            market="KR",
            date=D,
            stage=f"20_debate_r0_{side}",
            payload=ok_turn(side),
            agent="r",
        )
    tools.checkpoint_summarize(ctx, ticker="005930", market="KR", date=D)
    exec_ledger.snapshot_positions(
        ctx.conn,
        account_id="paper-main",
        d=D,
        market="KR",
        nav=Decimal("1000000"),
        cash=Decimal("500000"),
        positions=[
            {
                "ticker": "005930",
                "market": "KR",
                "qty": 5,
                "avg_price": "70000",
                "current_price": "71500",
                "eval_amount": "357500",
            }
        ],
    )
    r = tools.checkpoint_bundle(ctx, ticker="005930", market="KR", date=D, kind="trader")
    pf = r["data"]["context"]["portfolio"]
    assert pf["paper-main"]["nav"] == "1000000" and pf["paper-main"]["positions"][0]["qty"] == 5
    assert pf["paper-main"]["mode"] == "auto" and pf["real-main"]["positions"] == []


def test_checkpoint_write_records_model(ctx, project):
    _inputs(project)
    r = tools.checkpoint_write(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="10_analyst_technical",
        payload=ok_report(),
        agent="analyst-technical",
        model="haiku",
    )
    assert r["ok"]
    saved = read_checkpoint(run_dir(D, "KR", "005930"), "10_analyst_technical")
    assert saved["call"]["model"] == "haiku"


def test_validate_ids_rejects_dots():
    for bad in (".", "..", "..."):
        with pytest.raises(tools.ToolInputError):
            tools._validate_ids(bad, "KR")
    tools._validate_ids("BRK.B", "US")  # 점이 섞인 정상 티커는 허용


def test_market_dir_helper_creates_dir(project):
    d = run_store.market_dir(date(2026, 10, 9), "KR", None, create=True)
    assert d.is_dir() and d.name == "KR" and d.parent.name == "2026-10-09"
    assert not run_store.market_dir(date(2026, 10, 10), "US", None).exists()


def _ready_for_submit(ctx, project):
    """보고서 2개·토론·요약·00_inputs 를 만들어 submit-decision 전제조건을 채운다."""
    _inputs(project)
    for role in ("technical", "fundamental"):
        tools.checkpoint_write(
            ctx,
            ticker="005930",
            market="KR",
            date=D,
            stage=f"10_analyst_{role}",
            payload=ok_report(role=role),
            agent="a",
        )
    for side in ("bull", "bear"):
        tools.checkpoint_write(
            ctx,
            ticker="005930",
            market="KR",
            date=D,
            stage=f"20_debate_r0_{side}",
            payload=ok_turn(side),
            agent="r",
        )
    tools.checkpoint_summarize(ctx, ticker="005930", market="KR", date=D)


def test_bundle_without_decision_has_no_previous_decision(ctx, project):
    _ready_for_submit(ctx, project)
    b = tools.checkpoint_bundle(ctx, ticker="005930", market="KR", date=D, kind="trader")
    assert b["ok"] and b["data"].get("previous_decision") is None


def test_section_read_for_fundamental_and_sentiment_includes_quote(ctx, project):
    """두 md 가 '함께 오는 quote 는 현재가' 라고 하므로 절 출력에 현재가를 실어 준다."""
    _inputs(project)
    for section in ("fundamental", "sentiment"):
        r = tools.checkpoint_read(
            ctx, ticker="005930", market="KR", date=D, stage="00_inputs", section=section
        )
        assert r["ok"] and r["data"]["quote"]["data"]["price"] == "71500", section


def test_requote_returns_change_pct(ctx, project):
    """Phase 25: 재시세 값의 당일 등락률을 함께 돌려준다(없으면 입력 시세의 값)."""
    from datetime import timedelta

    from trader.kernel.broker.fake import FakeMarket

    inputs = {
        "meta": {"asof": T0.isoformat()},
        "technical": {"quote": {"data": {"price": "100", "change_pct": 0.02}}},
    }
    ctx.market_client = None
    assert tools.requote(ctx, "KR", "005930", None, inputs, ctx.now()) == (
        Decimal("100"),
        None,
        0.02,
    )
    ctx.market_client = FakeMarket(prices={"005930": Decimal("112")})
    ctx.now = lambda: T0 + timedelta(minutes=11)
    price, note, pct = tools.requote(ctx, "KR", "005930", None, inputs, ctx.now())
    assert price == Decimal("112") and note == "재시세 100→112"
    assert pct == ctx.market_client._quote("005930", "KR").change_pct


ROLES4 = ("technical", "fundamental", "news_macro", "sentiment")


def _write_reports(ctx, signals, conf=0.8):
    for role, sig in zip(ROLES4, signals, strict=True):
        r = tools.checkpoint_write(
            ctx,
            ticker="005930",
            market="KR",
            date=D,
            stage=f"10_analyst_{role}",
            payload=ok_report(role=role, signal=sig, confidence=conf),
            agent=f"analyst-{role}",
        )
        assert r["ok"], r


def test_summarize_skips_debate_on_consensus(ctx, project):
    _inputs(project)
    _write_reports(ctx, ["BUY"] * 4)
    r = tools.checkpoint_summarize(ctx, ticker="005930", market="KR", date=D)
    assert r["ok"] and r["skipped_debate"] is True and r["reason"]
    s = read_checkpoint(run_dir(D, "KR", "005930"), "25_research_summary")["data"]
    assert s["skipped_debate"] is True and s["sycophantic"] is None
    # 트레이더 묶음은 생략 요약을 그대로 싣는다
    b = tools.checkpoint_bundle(ctx, ticker="005930", market="KR", date=D, kind="trader")
    assert b["ok"] and b["data"]["research_summary"]["skipped_debate"] is True
    # (submit-decision 이후 원장 단언은 Task 11 에서 이어 붙인다)


def test_summarize_requires_debate_without_consensus(ctx, project):
    _inputs(project)
    _write_reports(ctx, ["BUY", "BUY", "BUY", "SELL"])
    r = tools.checkpoint_summarize(ctx, ticker="005930", market="KR", date=D)
    assert r["ok"] is False and r["debate_required"] is True
    assert read_checkpoint(run_dir(D, "KR", "005930"), "25_research_summary") is None


def test_summarize_low_confidence_requires_debate(ctx, project):
    _inputs(project)
    _write_reports(ctx, ["BUY"] * 4, conf=0.6)
    r = tools.checkpoint_summarize(ctx, ticker="005930", market="KR", date=D)
    assert r["ok"] is False and r["debate_required"] is True


def test_summarize_skip_disabled_requires_debate(ctx, project):
    from trader.features.spot.settings import DebateCfg

    ctx.settings = ctx.settings.model_copy(update={"debate": DebateCfg(skip_on_consensus=False)})
    _inputs(project)
    _write_reports(ctx, ["BUY"] * 4)
    r = tools.checkpoint_summarize(ctx, ticker="005930", market="KR", date=D)
    assert r["ok"] is False and r["debate_required"] is True


def test_summarize_with_debate_turns_uses_debate_even_on_consensus(ctx, project):
    """토론 턴이 이미 있으면(합의여도) 수렴 감시를 포함한 정상 요약을 만든다."""
    _inputs(project)
    _write_reports(ctx, ["BUY"] * 4)
    for side in ("bull", "bear"):
        tools.checkpoint_write(
            ctx,
            ticker="005930",
            market="KR",
            date=D,
            stage=f"20_debate_r0_{side}",
            payload=ok_turn(side),
            agent="r",
        )
    r = tools.checkpoint_summarize(ctx, ticker="005930", market="KR", date=D)
    assert r["ok"] and not r.get("skipped_debate")
    s = read_checkpoint(run_dir(D, "KR", "005930"), "25_research_summary")["data"]
    assert "skipped_debate" not in s and isinstance(s["sycophantic"], bool)
    # (submit-decision 이후 decisions.skipped_debate == 0 단언은 Task 11 에서 이어 붙인다)


def test_trader_bundle_counts_evidence_before_dropping_it(ctx, project):
    """L10: 트레이더 묶음은 evidence 를 떼지만, 떼기 전 원본에서 역할별 근거 항목 수·출처 수·
    data_gaps 수·방향과 방향 일치도를 파이썬이 센다(검증된 근거 수가 아님을 정의에 명시)."""
    _realistic_reports_and_turns(ctx, project)
    b = tools.checkpoint_bundle(ctx, ticker="005930", market="KR", date=D, kind="trader")["data"]
    st = b["context"]["evidence_stats"]
    assert "검증" in st["definition"]
    tech = st["roles"]["technical"]
    assert tech == {
        "evidence": 5,
        "sources": 1,
        "data_gaps": 2,
        "counter_evidence": 2,
        "key_risks": 3,
        "direction": "BULLISH",
    }
    assert st["missing_roles"] == [] and st["directions"] == {"BULLISH": 4}
    assert st["agreement"] == 1.0
    assert "evidence" not in b["reports"][0]


def test_trader_bundle_holding_context_shows_min_hold(ctx, project):
    """19번(iii): 재평가 입력에 보유 거래일·최소 보유 충족 여부를 싣는다 — 익절가 도달이 최소 보유
    전이면 SELL/TRIM 이 게이트에서 거부될 수 있음을 트레이더가 안다."""
    _realistic_reports_and_turns(ctx, project)
    oid = exec_ledger.insert_order(
        ctx.conn,
        decision_id=None,
        account_id="paper-main",
        ticker="005930",
        market="KR",
        side="BUY",
        qty=1,
        order_type="LIMIT",
        price=Decimal("70000"),
        status="filled",
        order_no="1",
        message="",
        mode="auto",
        created_at=f"{D.isoformat()}T01:00:00+00:00",
    )
    ctx.conn.execute(
        "INSERT INTO fills(order_id, account_id, qty, price, filled_at) VALUES (?, ?, ?, ?, ?)",
        (oid, "paper-main", 1, "70000", f"{D.isoformat()}T01:00:00+00:00"),
    )
    ctx.conn.commit()
    b = tools.checkpoint_bundle(ctx, ticker="005930", market="KR", date=D, kind="trader")["data"]
    h = b["context"]["holding"]["paper-main"]
    assert h["held_trading_days"] == 0 and h["min_hold_days"] == 3 and h["sell_allowed"] is False
