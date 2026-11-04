"""`fo/session.run`(옛 `tests/fo/test_session.py`) + 세션 종류(옛 `orchestrator/test_session_fo.py`)
+ 에이전트 매핑·오케스트레이터 프롬프트(옛 `agents/test_schemas_fo.py` 3건) — 입력 저장 → fo 세션
1회 → `_fo_decision` 읽기, 상한(fo 가 세션 앞에서 검사, A6)·오늘 결정 멱등·enabled=false 생략·과거
asof 강제 dry-run, stdin 보충, 에이전트 이름 재사용."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.features.fo._support import (
    ACCT,
    NOW,
    D,
    _market,
    _master_with_chain,
    account,
    balance,
    ports_for,
    settings,
)
from trader.features.fo import session as fo_session
from trader.features.fo import store
from trader.features.fo.schemas import FoDecisionEnvelope
from trader.features.fo.session_kinds import FO, FO_AGENTS, FO_RESUME, agents_for_runner
from trader.kernel.broker.fake import FakeFoBroker
from trader.kernel.ledger import runs
from trader.kernel.session import calls
from trader.kernel.session.agents import agents_json, load_agent, orchestrator_prompt
from trader.kernel.session.run_store import list_checkpoints, market_dir, read_checkpoint
from trader.kernel.session.runner import PipelineError, SessionResult, SessionSpec, stdin_payload


def _deps(conn, *, now=NOW, enabled=True, **fo):
    acct = account()
    s = settings(enabled=enabled, **fo)
    broker = FakeFoBroker(acct, balance=balance(), now=lambda: now)
    ports = ports_for(s, [acct])
    market = _market(now=now)
    ports["accounts"] = SimpleNamespace(
        **{**vars(ports["accounts"]), "fo_brokers": lambda: {acct.id: broker},
           "market_client": lambda: market}
    )  # fmt: skip
    return dict(
        settings=s, conn=conn, ports=ports, master=_master_with_chain(), date=D,
        now=lambda: now, dry_run=False, state=None,
    )  # fmt: skip


def test_fo_session_writes_inputs_and_decision_fake(project, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    out = fo_session.run(**_deps(conn))
    assert out["skipped"] is None and out["session"]["ok"] and out["session"]["fake"]
    assert out["inputs_path"].endswith("_fo_inputs.json")
    stages = set(list_checkpoints(market_dir(D, "KR")))
    assert {"_fo_inputs", "_fo_analyst_technical", "_fo_analyst_sentiment",
            "_fo_analyst_news_macro", "_fo_debate_r0_bull", "_fo_debate_r0_bear",
            "_fo_summary", "_fo_decision", f"_fo_risk_check_{ACCT}"} <= stages  # fmt: skip
    assert out["decision"]["decision"]["direction"] == "FLAT"
    assert conn.execute("SELECT COUNT(*) AS n FROM runs WHERE kind='fo'").fetchone()["n"] == 1
    # 같은 날 다시 부르면 세션 없이 기존 결정을 돌려준다
    again = fo_session.run(**_deps(conn))
    assert again["skipped"] and "결정" in again["skipped"] and again["session"] is None
    assert again["decision"]["decision"]["direction"] == "FLAT"
    assert conn.execute("SELECT COUNT(*) AS n FROM runs WHERE kind='fo'").fetchone()["n"] == 1


def _real_fo_session(conn, at):
    """오늘 fo 세션 1건(실패 포함, FAKE 아님)을 원장에 남긴다."""
    run_id = runs.start(conn, "fo", "KR")
    calls.start_call(conn, run_id, agent="orchestrator", model="sonnet", ticker="", market="KR",
                     session_id=f"s{run_id}", now=at)  # fmt: skip
    runs.finish(conn, run_id, "error", error=None)


def test_fo_session_cap_blocks(project, conn):
    """A6: `extra_cap_key=None` 이라 커널은 fo 상한을 모른다 — fo `session.run` 이 세션을 띄우기
    전에 오늘 `runs.kind='fo'` 세션 수 ≥ `fo.session_cap` 이면 커널 `PipelineError`(러너 0회)."""
    seen: list = []

    def runner(*a, **kw):
        seen.append(a)
        return SessionResult(ok=True, session_id="x")

    deps = _deps(conn, session_cap=2)
    for _ in range(2):
        _real_fo_session(conn, NOW)
    with pytest.raises(PipelineError, match="상한"):
        fo_session.run(**deps, session_runner=runner)
    assert seen == []
    assert "_fo_decision" not in list_checkpoints(market_dir(D, "KR"))
    # 다른 종류 세션은 fo 상한에 세지 않는다
    deps2 = _deps(conn, session_cap=3)
    run_id = runs.start(conn, "analyze", "KR")
    calls.start_call(conn, run_id, agent="orchestrator", model="sonnet", ticker="005930",
                     market="KR", session_id="a1", now=NOW)  # fmt: skip
    fo_session.run(**deps2, session_runner=runner)
    assert len(seen) == 1


def test_fo_session_disabled_without_positions_skips(project, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    out = fo_session.run(**_deps(conn, enabled=False))
    assert out["skipped"] and "enabled" in out["skipped"] and out["inputs_path"] is None
    # 보유가 있으면 꺼져 있어도 세션은 돈다(청산 의견)
    with store.tx(conn):
        store.insert_position(
            conn, account_id=ACCT, code="105W11", kind="FUTURES", contract_side="LONG",
            direction="LONG", vehicle="FUTURES", status="held", qty_held=1, qty_target=1,
            stop_pt=345, nav_at_open=100000000, opened_at=NOW.isoformat(),
        )  # fmt: skip
    out2 = fo_session.run(**_deps(conn, enabled=False))
    assert out2["skipped"] is None and out2["session"]["ok"]


def test_fo_session_without_fo_accounts_skips(project, conn):
    deps = _deps(conn)
    deps["ports"]["accounts"].fo_accounts = lambda: []
    out = fo_session.run(**deps)
    assert out["skipped"] == "asset: fo 계좌 없음" and out["session"] is None


def test_fo_session_historical_asof_forces_dry_run(project, conn):
    """asof 가 now − max_input_age_s 보다 과거면 세션은 dry_run 강제 + 입력 historical."""
    seen = {}

    def runner(spec, kind, ctx, **kw):
        seen.update(spec=spec, kind=kind, ctx=ctx, kw=kw)
        return SessionResult(ok=True, session_id="x", fake=False)

    old = NOW - timedelta(hours=3)
    out = fo_session.run(**_deps(conn), session_runner=runner, asof=old)
    assert seen["spec"].kind == "fo" and seen["spec"].dry_run is True
    assert seen["spec"].asof == old and seen["spec"].market == "KR" and seen["spec"].tickers == []
    assert seen["kind"] is FO and seen["ctx"].dry_run is True and seen["ctx"].ext["fo"].dry_run
    assert seen["kw"]["agents"]["orchestrator"].name == "orchestrator"
    inp = read_checkpoint(market_dir(D, "KR"), "_fo_inputs")["data"]
    assert inp["meta"]["historical"] is True and out["forced_dry_run"] is True


# --- 세션 종류(A6) ------------------------------------------------------------------------


def test_stdin_payload_fo_resume_has_position_and_trigger():
    pos = {"account_id": ACCT, "position_id": 3, "expected_version": 2, "request_id": "r1"}
    spec = SessionSpec(
        market="KR", date=D, tickers=[], kind="fo_resume",
        extra={"position": pos, "trigger": {"kind": "move", "detail": {}}},
    )  # fmt: skip
    p = json.loads(stdin_payload(spec, FO_RESUME, NOW))
    assert p["kind"] == "fo_resume" and p["position"] == pos and p["trigger"]["kind"] == "move"
    spec2 = SessionSpec(market="KR", date=D, tickers=[], kind="fo")
    p2 = json.loads(stdin_payload(spec2, FO, NOW))
    assert "position" not in p2 and "trigger" not in p2 and p2["tickers"] == []


def test_session_kinds_declared():
    assert FO.kind == "fo" and FO.runs_kind == "fo" and FO.throttle == "hard"
    assert FO_RESUME.kind == "fo_resume" and FO_RESUME.runs_kind == "monitor"
    assert FO_RESUME.throttle == "soft"
    assert FO.extra_cap_key is None and FO_RESUME.extra_cap_key is None
    assert FO.agents == FO_RESUME.agents == FO_AGENTS == (
        "analyst-technical", "analyst-sentiment", "analyst-market", "researcher-bull",
        "researcher-bear", "fo-trader",
    )  # fmt: skip
    assert FO.schemas[-1] is FoDecisionEnvelope and FO_RESUME.schemas == (FoDecisionEnvelope,)
    assert FO.prompt_section == "fo" and FO_RESUME.prompt_section == "fo_resume"
    assert FO.stdin_extra is None and FO.retry_guard is False and FO_RESUME.retry_guard is False


# --- 에이전트(옛 test_schemas_fo 3건) ---------------------------------------------------------


def test_agents_for_runner_resolves_reused_names():
    """애널리스트·리서처는 spot·regime 정의를 이름으로 재사용하고 fo 는 md 를 새로 만들지 않는다.
    `orchestrator` 별칭은 fo-orchestrator.md 다."""
    from trader.features.fo.feature import FEATURE

    agents = agents_for_runner()
    assert set(FO_AGENTS) <= set(agents)
    assert {d.name for d in FEATURE.agents} == {"fo-trader", "fo-orchestrator"}
    assert agents["orchestrator"].path.name == "fo-orchestrator.md"
    assert agents["analyst-market"].path.parent.parent.name == "regime"
    assert agents["analyst-technical"].path.parent.parent.name == "spot"
    assert agents["fo-trader"].schema is FoDecisionEnvelope


def test_fo_agents_json_and_trader_md():
    j = agents_json(FO, agents_for_runner(), {})
    assert set(j) == set(FO_AGENTS)
    p = j["fo-trader"]["prompt"]
    assert p.count("스키마:") == 1 and '"expected_version"' in p and '"target_leverage"' in p
    a = load_agent(agents_for_runner()["fo-trader"])
    assert a.model == "opus" and "# 출력 규칙" in a.prompt
    for token in ("HOLD", "FLAT", "max_leverage", "premium_stop_mult", "vs_spot", "stop_pt"):
        assert token in a.prompt, token


def test_fo_trader_md_schema_matches_model():
    """md 의 스키마 사본은 정본 모델(`FoDecisionEnvelope`)과 같다."""
    from trader.kernel.session.agents import json_schema

    md = load_agent(agents_for_runner()["fo-trader"]).prompt
    line = next(ln for ln in md.splitlines() if ln.startswith("스키마: "))
    assert line[len("스키마: "):] == json_schema(FoDecisionEnvelope)


def test_orchestrator_prompt_has_fo_mode_and_schema():
    """kind 별 프롬프트: fo 는 `## fo 모드`, fo_resume 은 `## fo_resume 모드` 절만."""
    agents = agents_for_runner()
    p = orchestrator_prompt(FO, agents, debate_rounds=1, checkpoint_names=fo_session.CHECKPOINTS)
    pr = orchestrator_prompt(FO_RESUME, agents, debate_rounds=1, checkpoint_names=())
    assert "## fo 모드" in p and "## fo_resume 모드" not in p
    assert "## fo_resume 모드" in pr and "## fo 모드" not in pr
    assert "## market 모드" not in p and "submit-decision " not in p
    both = p + pr
    for token in (
        "tool fo bundle --kind analyst",
        "--role technical",
        "_fo_analyst_",
        "tool fo write",
        "tool fo bundle --kind researcher",
        "_fo_debate_r0_",
        "tool fo summarize",
        "tool fo bundle --kind trader",
        "tool submit-fo-decision",
        "FoDecisionEnvelope 스키마",
    ):
        assert token in both, token
    o = load_agent(agents["orchestrator"]).prompt
    assert "position" in o.split("## fo_resume 모드", 1)[1].split("## ", 1)[0]
