"""`tool fo bundle|write|summarize`·`submit-fo-decision`(옛 `orchestrator/test_tools_fo.py` 14건) —
쓰기 허용 목록·파일명↔페이로드·요약 무효화·제출 전제·봉투 대조·버전·계약 해석·노후 입력·멱등·진입 창
밖 보류(Review Focus 4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from tests.features.fo._support import (
    ACCT,
    NOW,
    D,
    _ctx,
    _debate,
    _env,
    _fo,
    _ready,
    _reports,
    _tool_ctx,
    settings,
)
from tests.features.spot.helpers import ok_report, ok_turn
from trader.features.fo import store, tools
from trader.features.fo.feature import FEATURE
from trader.kernel.ledger import control
from trader.kernel.session.run_store import list_checkpoints, market_dir, read_checkpoint
from trader.kernel.session.stages import check_rule, first_rule

EARLY = datetime(2026, 10, 26, 23, 30, tzinfo=UTC)  # 08:30 KST — 진입 창(09:00~) 밖


@pytest.fixture
def ctx(project, conn):
    fo, inp = _fo(conn)
    return _tool_ctx(conn, fo, inp)


def test_feature_declares_fo_tools_and_rules():
    tc = {t.name: t for t in FEATURE.tools}
    assert set(tc) == {"fo", "submit-fo-decision"}
    assert tc["fo"].handler is tools.fo_tool_app and tc["fo"].stage_rules == tools.FO_WRITE_RULES
    assert tc["submit-fo-decision"].handler is tools.submit_fo_decision_cmd
    assert tc["submit-fo-decision"].stage_rules == (tools.FO_SUBMIT_RULE,)
    for stage in ("_fo_analyst_technical", "_fo_debate_r1_bear"):
        assert first_rule(tools.FO_WRITE_RULES, stage) is not None, stage
    for stage in ("_fo_summary", "_fo_decision", "_fo_inputs"):
        assert first_rule(tools.FO_WRITE_RULES, stage) is None, stage
    ready = ["_fo_inputs", "_fo_analyst_technical", "_fo_analyst_sentiment",
             "_fo_debate_r0_bull", "_fo_debate_r0_bear", "_fo_summary"]  # fmt: skip
    assert check_rule(tools.FO_SUBMIT_RULE, "_fo_decision", ready) == []
    assert check_rule(tools.FO_SUBMIT_RULE, "_fo_decision_r1_paper-fo", ready) == []
    assert check_rule(tools.FO_SUBMIT_RULE, "_fo_decision", ready[:2] + ready[3:]) != []


def test_fo_write_allowlist_and_name_mismatch(ctx):
    rep = ok_report(ticker="KOSPI200", role="technical")
    assert tools.fo_write(ctx, date=D, stage="_fo_decision", payload={})["ok"] is False
    assert tools.fo_write(ctx, date=D, stage="_fo_summary", payload={})["ok"] is False
    assert tools.fo_write(ctx, date=D, stage="_fo_analyst_fundamental", payload=rep)["ok"] is False
    bad = tools.fo_write(ctx, date=D, stage="_fo_analyst_sentiment", payload=rep)
    assert bad["ok"] is False and "role" in bad["error"]
    ok = tools.fo_write(
        ctx, date=D, stage="_fo_analyst_technical", payload=rep, agent="analyst-technical"
    )
    saved = read_checkpoint(market_dir(D, "KR"), "_fo_analyst_technical")
    assert ok["ok"] and saved["call"]["agent"] == "analyst-technical"
    mm = tools.fo_write(ctx, date=D, stage="_fo_debate_r0_bull", payload=ok_turn("bear", 0))
    assert mm["ok"] is False and "불일치" in mm["error"]
    far = tools.fo_write(ctx, date=D, stage="_fo_debate_r7_bull", payload=ok_turn("bull", 7))
    assert far["ok"] is False and "debate_rounds" in far["error"]
    # HTML 엔티티 언이스케이프
    esc = ok_report(ticker="KOSPI200", role="sentiment", summary="외인 &gt; 기관")
    w = tools.fo_write(ctx, date=D, stage="_fo_analyst_sentiment", payload=esc)
    assert w["ok"]
    saved = read_checkpoint(market_dir(D, "KR"), "_fo_analyst_sentiment")
    assert saved["data"]["summary"] == "외인 > 기관"


def test_fo_write_requires_inputs(project, conn):
    fo = _ctx(conn)
    c = tools.FoToolContext(settings=settings(), conn=conn, now=lambda: NOW, fo=fo)
    r = tools.fo_write(
        c, date=D, stage="_fo_analyst_technical", payload=ok_report(role="technical")
    )
    assert r["ok"] is False and "_fo_inputs" in r["error"]
    assert tools.fo_bundle(c, date=D, kind="analyst", role="technical")["ok"] is False


def test_fo_write_invalidates_summary(ctx):
    _ready(ctx)
    assert "_fo_summary" in list_checkpoints(market_dir(D, "KR"))
    _debate(ctx, rounds=(1,))
    assert "_fo_summary" not in list_checkpoints(market_dir(D, "KR"))
    s = tools.fo_summarize(ctx, date=D)
    assert s["ok"] and s["rounds"] == 1


def test_fo_bundle_shapes(ctx):
    a = tools.fo_bundle(ctx, date=D, kind="analyst", role="technical")
    assert a["ok"] and a["data"]["role"] == "technical" and "indicators" in a["data"]["technical"]
    m = tools.fo_bundle(ctx, date=D, kind="analyst", role="news_macro")
    assert m["ok"] and m["data"]["proxy"] == "KOSPI200" and "regime" in m["data"]
    assert tools.fo_bundle(ctx, date=D, kind="researcher", round=0, side="bull")["ok"] is False
    _reports(ctx)
    r0 = tools.fo_bundle(ctx, date=D, kind="researcher", round=0, side="bull")
    assert r0["ok"] and len(r0["data"]["reports"]) == 2 and r0["data"]["own_previous"] is None
    assert tools.fo_bundle(ctx, date=D, kind="researcher", round=1, side="bull")["ok"] is False
    _debate(ctx)
    r1 = tools.fo_bundle(ctx, date=D, kind="researcher", round=1, side="bear")
    assert r1["ok"] and r1["data"]["opponent_previous"]["side"] == "bull"
    assert tools.fo_bundle(ctx, date=D, kind="trader")["ok"] is False  # 요약 없음
    tools.fo_summarize(ctx, date=D)
    t = tools.fo_bundle(ctx, date=D, kind="trader")
    assert t["ok"] and t["data"]["request_id"] == ctx._inputs["meta"]["request_id"]
    assert t["data"]["context"]["position"] is None and t["data"]["previous_decision"] is None
    assert t["data"]["context"]["limits"]["max_leverage"] == 2.0


def test_submit_requires_two_roles_debate_summary(ctx):
    r = tools.submit_fo_decision(ctx, date=D, payload=_env(ctx))
    assert r["ok"] is False and "역할" in r["error"]
    _reports(ctx, roles=("technical",))
    r = tools.submit_fo_decision(ctx, date=D, payload=_env(ctx))
    assert r["ok"] is False and "역할" in r["error"]
    _reports(ctx, roles=("sentiment",))
    r = tools.submit_fo_decision(ctx, date=D, payload=_env(ctx))
    assert r["ok"] is False and "r0" in r["error"]
    _debate(ctx)
    r = tools.submit_fo_decision(ctx, date=D, payload=_env(ctx))
    assert r["ok"] is False and "_fo_summary" in r["error"]
    assert "_fo_decision" not in list_checkpoints(market_dir(D, "KR"))


def test_submit_morning_executes_in_window_and_records(ctx, conn):
    _ready(ctx)
    bad = tools.submit_fo_decision(ctx, date=D, payload={"decision": {"intent": "OPEN"}})
    assert bad["ok"] is False and "스키마" in bad["error"]
    r = tools.submit_fo_decision(ctx, date=D, payload=_env(ctx))
    assert r["ok"] and r["stage"] == "_fo_decision" and r["executed"] is True, r
    dec = read_checkpoint(market_dir(D, "KR"), "_fo_decision")
    res = dec["data"]["resolved"]
    assert (res["code"], res["kind"], res["index_at_decision"]) == ("105W11", "FUTURES", "350")
    assert dec["call"]["agent"] == "fo-trader"
    row = conn.execute("SELECT * FROM fo_decisions").fetchone()
    assert (row["intent"], row["direction"], row["round"]) == ("OPEN", "LONG", 0)
    assert row["account_id"] is None and row["index_at_decision"] == "350"
    assert json.loads(row["data"])["decision"]["stop_pt"] == 345.0
    stages = list_checkpoints(market_dir(D, "KR"))
    assert f"_fo_risk_check_{ACCT}" in stages and f"_fo_order_{ACCT}" in stages
    assert store.open_positions(conn, ACCT)[0].status == "entering"
    assert r["executions"][ACCT][0]["status"] == "accepted"


def test_submit_envelope_mismatch_rejected(ctx):
    _ready(ctx)
    bad = tools.submit_fo_decision(ctx, date=D, payload={**_env(ctx), "account_id": ACCT})
    assert bad["ok"] is False and "account_id" in bad["error"]
    bad = tools.submit_fo_decision(ctx, date=D, payload={**_env(ctx), "request_id": "other"})
    assert bad["ok"] is False and "request_id" in bad["error"]


def test_submit_resolves_option_code_and_index(ctx, conn):
    _ready(ctx)
    env = _env(ctx, vehicle="OPTION_BUY", direction="SHORT", strike_rule="OTM1", entry_price=5.0,
               target_leverage=0.5)  # fmt: skip
    r = tools.submit_fo_decision(ctx, date=D, payload=env)
    assert r["ok"], r
    res = read_checkpoint(market_dir(D, "KR"), "_fo_decision")["data"]["resolved"]
    assert res["kind"] == "PUT" and res["code"].startswith("305W11") and res["strike"] == "348"
    assert res["multiplier"] == 50000 and res["days_left"] is not None


def test_submit_stale_inputs_records_only(project, conn):
    old = NOW - timedelta(seconds=settings().fo.max_input_age_s + 1)
    fo, inp = _fo(conn, asof=old)
    ctx = _tool_ctx(conn, fo, inp)
    _ready(ctx)
    r = tools.submit_fo_decision(ctx, date=D, payload=_env(ctx))
    assert r["ok"] and r["executed"] is False and "노후" in r["note"]
    rc = read_checkpoint(market_dir(D, "KR"), f"_fo_risk_check_{ACCT}")
    assert rc["data"]["approved"] is False and "입력 노후 — 실행 안 함" in rc["data"]["note"]
    assert store.open_positions(conn, ACCT) == []
    assert control.get_flag(conn, f"fo_pending_entry:{ACCT}") is None


def test_submit_idempotent(ctx, conn):
    _ready(ctx)
    first = tools.submit_fo_decision(ctx, date=D, payload=_env(ctx))
    again = tools.submit_fo_decision(ctx, date=D, payload=_env(ctx, direction="SHORT"))
    assert again["ok"] and again["already"] is True and again["direction"] == "LONG"
    assert conn.execute("SELECT COUNT(*) AS n FROM fo_decisions").fetchone()["n"] == 1
    assert len(ctx.fo.brokers[ACCT].orders) == 1 and first["executed"]


def test_submit_outside_window_parks_entry(project, conn):
    """Review Focus 4 전반부: 진입 창 밖(08:30 KST) 아침 결정은 주문 없이
    `control fo_pending_entry:<계좌>` 에 {date, stage, envelope, request_id, requested_at, dry_run}
    으로 보류된다(판정 파일도 쓰지 않는다)."""
    fo, inp = _fo(conn, now=EARLY)
    ctx = _tool_ctx(conn, fo, inp, EARLY)
    _ready(ctx)
    r = tools.submit_fo_decision(ctx, date=D, payload=_env(ctx))
    assert r["ok"] and r["executed"] is False and r["pending"] == [ACCT]
    flag = control.get_flag(conn, f"fo_pending_entry:{ACCT}")
    assert set(flag) == {"date", "stage", "envelope", "request_id", "requested_at", "dry_run"}
    assert flag["date"] == D.isoformat() and flag["stage"] == "_fo_decision"
    assert flag["envelope"]["decision"]["direction"] == "LONG"
    assert flag["request_id"] == inp["meta"]["request_id"] and flag["dry_run"] is False
    assert flag["requested_at"] == EARLY.isoformat()
    assert f"_fo_risk_check_{ACCT}" not in list_checkpoints(market_dir(D, "KR"))
    assert ctx.fo.brokers[ACCT].orders == []
    assert tools.fo_pending_entry(conn, ACCT, D) == flag
    r2 = tools.submit_fo_decision(ctx, date=D, payload=_env(ctx), stage="_fo_decision")
    assert r2["already"]


def test_submit_flat_hold_records_without_broker(ctx, conn):
    _ready(ctx)
    env = _env(ctx, intent="HOLD", direction="FLAT", target_leverage=0.0, stop_pt=None,
               entry_price=None, take_profit_pt=None)  # fmt: skip
    r = tools.submit_fo_decision(ctx, date=D, payload=env)
    assert r["ok"] and r["executed"] is False and r["direction"] == "FLAT"
    rc = read_checkpoint(market_dir(D, "KR"), f"_fo_risk_check_{ACCT}")
    assert rc["data"]["approved"] is True and "관망" in rc["data"]["note"]
    assert "place_order" not in ctx.fo.brokers[ACCT].calls


def test_reeval_request_and_submit_round(ctx, conn):
    """재평가: 파이썬이 `_fo_reeval_r<n>_<acct>` 요청을 남기고, 제출 봉투는 그 요청(계좌·포지션·
    버전·request_id)과 같아야 하며 포지션 버전이 바뀌었으면 거부한다."""
    _ready(ctx)
    assert tools.submit_fo_decision(ctx, date=D, payload=_env(ctx))["ok"]
    pos = store.open_positions(conn, ACCT)[0]
    req = tools.fo_reeval_request(
        ctx, date=D, account_id=ACCT, position_id=pos.id, trigger={"kind": "move", "detail": {}}
    )
    assert req["ok"] and req["round"] == 1 and req["stage"] == f"_fo_reeval_r1_{ACCT}"
    position = req["position"]
    assert position["expected_version"] == pos.version and position["request_id"]
    b = tools.fo_bundle(ctx, date=D, kind="trader", position_id=pos.id, account=ACCT)
    assert b["ok"] and b["data"]["context"]["position"]["id"] == pos.id
    assert b["data"]["trigger"]["kind"] == "move" and b["data"]["next_round"] == 1
    assert b["data"]["request_id"] == position["request_id"]
    env = {
        "decision": _env(ctx, intent="HOLD")["decision"], "request_id": position["request_id"],
        "account_id": ACCT, "position_id": pos.id, "expected_version": pos.version,
    }  # fmt: skip
    stage = f"_fo_decision_r1_{ACCT}"
    wrong = tools.submit_fo_decision(
        ctx, date=D, payload={**env, "expected_version": 99}, stage=stage
    )
    assert wrong["ok"] is False and "expected_version" in wrong["error"]
    wrong2 = tools.submit_fo_decision(ctx, date=D, payload={**env, "position_id": 77}, stage=stage)
    assert wrong2["ok"] is False
    applied = []
    ctx.fo.reeval_apply = lambda e, r: applied.append((e.position_id, r)) or {"applied": True}
    ok = tools.submit_fo_decision(ctx, date=D, payload=env, stage=stage)
    assert ok["ok"] and ok["round"] == 1 and ok["applied"] == {"applied": True}
    assert applied == [(pos.id, 1)]
    row = conn.execute("SELECT * FROM fo_decisions WHERE round=1").fetchone()
    assert (row["account_id"], row["position_id"], row["intent"]) == (ACCT, pos.id, "HOLD")
    # 버전이 바뀐 뒤 같은 요청의 재제출은 거부
    with store.tx(conn):
        store.transition(conn, pos.id, expected_version=pos.version, note="x")
    req2 = tools.fo_reeval_request(
        ctx, date=D, account_id=ACCT, position_id=pos.id, trigger={"kind": "news"}
    )
    stage2 = f"_fo_decision_r2_{ACCT}"
    stale = tools.submit_fo_decision(
        ctx, date=D, payload={**env, "request_id": req2["position"]["request_id"]}, stage=stage2
    )
    assert stale["ok"] is False and "버전" in stale["error"]


def _invoke(app, args):
    r = CliRunner().invoke(app, args)
    return r, json.loads(r.output.strip().splitlines()[-1])


def test_cli_tool_fo_commands_emit_json(project, conn, monkeypatch):
    fo, inp = _fo(conn)
    c = _tool_ctx(conn, fo, inp)
    monkeypatch.setattr(tools, "_open_ctx", lambda: (c, None))
    r, out = _invoke(tools.fo_tool_app, ["bundle", "--market", "KR", "--date", D.isoformat(),
                                         "--kind", "analyst", "--role", "technical"])  # fmt: skip
    assert r.exit_code == 0, r.output
    assert out["data"]["role"] == "technical"
    rep = json.dumps(ok_report(ticker="KOSPI200", role="technical"))
    w, wout = _invoke(tools.fo_tool_app, ["write", "--market", "KR", "--date", D.isoformat(),
                                          "--stage", "_fo_analyst_technical", "--agent",
                                          "analyst-technical", "--json", rep])  # fmt: skip
    assert w.exit_code == 0 and wout["ok"]
    s, sout = _invoke(tools.fo_tool_app, ["summarize", "--market", "KR", "--date", D.isoformat()])
    assert s.exit_code == 1 and sout["ok"] is False
    import typer

    app = typer.Typer()
    app.command("submit-fo-decision")(tools.submit_fo_decision_cmd)
    app.command("noop")(lambda: None)
    payload = json.dumps({"decision": {"intent": "HOLD", "direction": "FLAT",
                                       "vehicle": "FUTURES"}, "request_id": "x"})  # fmt: skip
    sub, subout = _invoke(app, ["submit-fo-decision", "--market", "KR", "--date", D.isoformat(),
                                "--json", payload])  # fmt: skip
    assert sub.exit_code == 1 and "역할" in subout["error"]
