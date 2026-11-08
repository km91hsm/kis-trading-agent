"""FAKE 파생 세션(`fo/fake.run_fake_fo`, 옛 `orchestrator/test_session_fo.test_fake_fo_writes_flat_
decision`) — 애널리스트 3 → r0 토론 → 요약 → FLAT HOLD 봉투(request_id 는 입력의 것), fo_resume 은
stdin 봉투(계좌·포지션·버전·request_id)를 그대로 되돌려 `_fo_decision_r<n>_<계좌>`."""

from __future__ import annotations

from tests.features.fo._support import ACCT, NOW, D, _fo, _tool_ctx
from trader.features.fo import store, tools
from trader.features.fo.fake import run_fake_fo
from trader.features.fo.session_kinds import FO, FO_RESUME, agents_for_runner
from trader.kernel.lane.types import LaneContext
from trader.kernel.session.run_store import list_checkpoints, market_dir, read_checkpoint
from trader.kernel.session.runner import SessionSpec, run_session

MORNING = [
    "_fo_analyst_technical", "_fo_analyst_sentiment", "_fo_analyst_news_macro",
    "_fo_debate_r0_bull", "_fo_debate_r0_bear", "_fo_summary", "_fo_decision",
]  # fmt: skip


def _lane(fo):
    return LaneContext(
        settings=fo.settings, conn=fo.conn, state=None, now=lambda: NOW, dry_run=fo.dry_run,
        notifier=fo.notifier, ports=fo.ports, ext={"fo": fo},
    )  # fmt: skip


def test_run_fake_fo_stage_list(project, conn):
    fo, inp = _fo(conn)
    spec = SessionSpec(market="KR", date=D, tickers=[], dry_run=False, asof=NOW, kind="fo")
    assert run_fake_fo(_lane(fo), spec) == MORNING


def test_fake_fo_writes_flat_decision(project, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    fo, inp = _fo(conn)
    spec = SessionSpec(market="KR", date=D, tickers=[], dry_run=False, asof=NOW, kind="fo")
    res = run_session(spec, FO, _lane(fo), agents=agents_for_runner())
    assert res.ok and res.fake and "_fo_decision" in res.stages_written
    stages = list_checkpoints(market_dir(D, "KR"))
    assert set(MORNING) <= set(stages)
    dec = read_checkpoint(market_dir(D, "KR"), "_fo_decision")["data"]
    assert dec["decision"]["intent"] == "HOLD" and dec["decision"]["direction"] == "FLAT"
    assert dec["request_id"] == inp["meta"]["request_id"] and dec["account_id"] is None
    rc = read_checkpoint(market_dir(D, "KR"), f"_fo_risk_check_{ACCT}")["data"]
    assert "관망" in rc["note"]
    assert conn.execute("SELECT COUNT(*) AS n FROM runs WHERE kind='fo'").fetchone()["n"] == 1
    # 재평가: 가짜 포지션 + 요청 → 봉투를 그대로 되돌린다
    with store.tx(conn):
        pid = store.insert_position(
            conn, account_id=ACCT, code="105W11", kind="FUTURES", contract_side="LONG",
            direction="LONG", vehicle="FUTURES", status="held", qty_held=1, qty_target=1,
            stop_pt=345, nav_at_open=100000000, opened_at=NOW.isoformat(),
        )  # fmt: skip
    ctx = _tool_ctx(conn, fo, inp)
    req = tools.fo_reeval_request(
        ctx, date=D, account_id=ACCT, position_id=pid, trigger={"kind": "move"}
    )
    spec2 = SessionSpec(
        market="KR", date=D, tickers=[], kind="fo_resume", dry_run=False, asof=NOW,
        extra={"position": req["position"], "trigger": {"kind": "move"}},
    )  # fmt: skip
    applied = []
    fo.reeval_apply = lambda e, r: applied.append(r) or {"applied": True}
    res2 = run_session(spec2, FO_RESUME, _lane(fo), agents=agents_for_runner())
    assert res2.ok and "_fo_decision_r1_paper-fo" in res2.stages_written and applied == [1]
    r1 = read_checkpoint(market_dir(D, "KR"), "_fo_decision_r1_paper-fo")["data"]
    ver = store.get_position(conn, pid).version
    assert (r1["account_id"], r1["position_id"], r1["expected_version"]) == (ACCT, pid, ver)
    assert r1["request_id"] == req["position"]["request_id"] and r1["decision"]["intent"] == "HOLD"
    assert conn.execute("SELECT COUNT(*) AS n FROM runs WHERE kind='monitor'").fetchone()["n"] == 1


def test_fake_without_fo_context_is_error_result(project, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    fo, inp = _fo(conn)
    lane = _lane(fo)
    lane.ext.clear()
    spec = SessionSpec(market="KR", date=D, tickers=[], dry_run=False, kind="fo")
    res = run_session(spec, FO, lane, agents=agents_for_runner())
    assert res.ok is False and "파생 실행 컨텍스트" in (res.error or "")


def test_fo_fake_cross_debate_writes_both_lanes(project, conn, monkeypatch):
    """Codex 켜짐 설정 + FAKE: r0 두 판 4턴과 by_lane 요약(실제 Codex 는 띄우지 않는다)."""
    import pytest

    from trader.kernel.session import codex

    monkeypatch.setenv("FAKE_CLAUDE", "1")
    monkeypatch.setattr(codex, "run_turn", lambda *a, **kw: pytest.fail("FAKE launched Codex"))
    fo, inp = _fo(conn)
    fo.settings.llm.codex_debate.enabled = True
    spec = SessionSpec(market="KR", date=D, tickers=[], dry_run=False, asof=NOW, kind="fo")
    written = run_fake_fo(_lane(fo), spec)
    assert written == [
        *MORNING[:3], "_fo_debate_r0_bull", "_fo_debate_r0_bear",
        "_fo_debatex_r0_bull", "_fo_debatex_r0_bear", "_fo_summary", "_fo_decision",
    ]  # fmt: skip
    d = market_dir(D, "KR")
    assert read_checkpoint(d, "_fo_debatex_r0_bull")["call"]["agent"] == "researcher-bull"
    summ = read_checkpoint(d, "_fo_summary")["data"]
    assert set(summ["by_lane"]) == {"a", "b"} and summ["lanes"] == ["a", "b"]


def test_fo_fake_without_codex_writes_single_lane(project, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    fo, inp = _fo(conn)
    fo.settings.llm.codex_debate.enabled = False
    spec = SessionSpec(market="KR", date=D, tickers=[], dry_run=False, asof=NOW, kind="fo")
    assert run_fake_fo(_lane(fo), spec) == MORNING
    assert "by_lane" not in read_checkpoint(market_dir(D, "KR"), "_fo_summary")["data"]
