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
    p.start(conn, s, d, SPOT_RESEARCH, now=NOW, version=1)
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


def _debate_turn(side, rnd):
    """반박 coverage 를 갖춘 합성 토론 턴(쟁점 ID `<side>:case`)."""
    other = "bull" if side == "bear" else "bear"
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
                "issue_id": f"{other}:case",
                "response": "상대 주제 반박",
                "evidence": ["technical.indicators"],
            }
        ]
        if rnd
        else [],
        rebuttals=["상호반박"] if rnd else [],
    )


def _codex_fake(d, seen=None):
    """가짜 Codex 실행기: 입력의 side 로 응답 측을 고르고 그 측 PROMPT·수령 기록을 확인한다."""
    import hashlib
    import json
    import subprocess
    from pathlib import Path

    from trader.kernel.session import codex

    def fake(cmd, **kw):
        data = json.loads(kw["input"].split("\n", 1)[1])
        side = data["side"]
        assert kw["input"].startswith(codex.PROMPTS[side])
        digest = hashlib.sha256(
            json.dumps(
                data["input_context"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        receipts = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (d / "input_receipts").glob("*.json")
        ]
        assert any(row["context_hash"] == digest and row["provider"] == "codex" for row in receipts)
        if data["round"] == 0:
            assert data["opponent_previous"] is None and data["own_previous"] is None
        else:
            assert data["opponent_previous"]["side"] == ("bull" if side == "bear" else "bear")
            assert data["own_previous"]["side"] == side
        if seen is not None:
            seen.append(data)
        response = _debate_turn(side, data["round"])
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text(
            json.dumps(response), encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return fake


def _seed_scope(d, scope):
    r = RESEARCHES[scope]
    write_checkpoint(
        d,
        r.input_stage,
        checkpoint_payload({"market": "KR", "proxy": "005930", "asof": NOW.isoformat()}),
    )
    for role in ("technical", "sentiment"):
        stage = r.report_prefix + role if scope != "market" else r.report_prefix
        write_checkpoint(d, stage, checkpoint_payload(ok_report(role=role)))


@pytest.mark.parametrize("scope", ["spot", "market", "fo"])
def test_actual_bridge_rounds_complete_and_changed_turn_rejected(
    conn, tmp_path, monkeypatch, scope
):
    """v1(약세만 Codex) 레거시 회귀: 판 a 하나, 옛 bear PROMPT 증명."""
    from pathlib import Path

    from trader.kernel.session import research_policy as p

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    s = settings()
    d = tmp_path
    p.start(conn, s, d, RESEARCHES[scope], now=NOW, version=1)
    r = RESEARCHES[scope]
    prefix = r.turn_prefix
    _seed_scope(d, scope)
    ctx = SimpleNamespace(conn=conn, settings=s, now=lambda: NOW)
    fake = _codex_fake(d)

    for rnd in (0, 1):
        assert p.write_error(conn, s, d, RESEARCHES[scope], round_no=rnd, side="bull") is None
        write_checkpoint(
            d,
            f"{prefix}{rnd}_bull",
            checkpoint_payload(_debate_turn("bull", rnd), {"provider": "claude"}),
        )
        out = p.generate_turn(
            ctx, d, RESEARCHES[scope], rnd, runner=fake, bin_path=Path("/fake/codex")
        )
        assert out["ok"], out
    assert p.completion_error(conn, s, d, RESEARCHES[scope]) is None
    write_checkpoint(
        d,
        f"{prefix}0_bull",
        checkpoint_payload(
            {**_debate_turn("bull", 0), "arguments": ["changed"]}, {"provider": "claude"}
        ),
    )
    assert "증명" in p.completion_error(conn, s, d, RESEARCHES[scope])


def _cross_setup(conn, tmp_path, scope="spot"):
    from trader.kernel.session import research_policy as p

    s = settings()
    d = tmp_path
    p.start(conn, s, d, RESEARCHES[scope], now=NOW)
    _seed_scope(d, scope)
    return s, d, RESEARCHES[scope], SimpleNamespace(conn=conn, settings=s, now=lambda: NOW)


def _write_claude(d, r, lane, rnd, side):
    write_checkpoint(
        d,
        r.turn_stage(lane, rnd, side),
        checkpoint_payload(_debate_turn(side, rnd), {"provider": "claude"}),
    )


def _run_cross(conn, s, d, r, ctx, fake, *, skip=()):
    """라운드마다 Claude 두 칸을 쓰고 Codex 두 칸을 생성한다. 마지막 라운드 결과를 돌려준다."""
    from pathlib import Path

    from trader.kernel.session import research_policy as p

    out_bear = out_bull = None
    for rnd in (0, 1):
        for lane, side in (("a", "bull"), ("b", "bear")):
            assert p.write_error(conn, s, d, r, round_no=rnd, side=side, lane=lane) is None
            if (lane, rnd, side) not in skip:
                _write_claude(d, r, lane, rnd, side)
        out_bear = p.generate_turn(
            ctx, d, r, rnd, side="bear", runner=fake, bin_path=Path("/fake/codex")
        )
        assert out_bear["ok"], out_bear
        if ("b", rnd, "bear") in skip:
            continue
        out_bull = p.generate_turn(
            ctx, d, r, rnd, side="bull", runner=fake, bin_path=Path("/fake/codex")
        )
        assert out_bull["ok"], out_bull
    return out_bear, out_bull


@pytest.mark.parametrize("scope", ["spot", "market", "fo"])
def test_cross_rounds_complete_with_two_codex_sides(conn, tmp_path, monkeypatch, scope):
    from trader.kernel.session import research_policy as p

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    s, d, r, ctx = _cross_setup(conn, tmp_path, scope)
    seen = []
    out_bear, out_bull = _run_cross(conn, s, d, r, ctx, _codex_fake(d, seen))
    assert out_bear["ok"] and out_bear["lane"] == "a" and out_bear["side"] == "bear"
    assert out_bull["ok"] and out_bull["lane"] == "b" and out_bull["side"] == "bull"
    assert out_bull["provider"] == "codex" and out_bull["round"] == 1
    assert out_bull["path"].endswith(r.turn_stage("b", 1, "bull") + ".json")
    assert read_checkpoint(d, r.turn_stage("b", 1, "bull"))["call"]["agent"] == r.bull_agent
    assert read_checkpoint(d, r.turn_stage("a", 1, "bear"))["call"]["agent"] == r.bear_agent
    assert read_checkpoint(d, r.turn_stage("b", 0, "bull"))["call"]["provider"] == "codex"
    assert [(x["round"], x["side"]) for x in seen] == [
        (0, "bear"),
        (0, "bull"),
        (1, "bear"),
        (1, "bull"),
    ]
    assert all("lane" not in x for x in seen)
    stages = {
        __import__("json").loads(f.read_text(encoding="utf-8"))["stage"]
        for f in (d / "input_receipts").glob("*.json")
    }
    assert f"{r.scope}:researcher:r1:bear" in stages
    assert f"{r.scope}:researcher:r1:bull:b" in stages
    assert p.completion_error(conn, s, d, r) is None
    # 판 b Codex 강세 턴 변조 → 증명 오류
    write_checkpoint(
        d,
        r.turn_stage("b", 0, "bull"),
        checkpoint_payload(
            {**_debate_turn("bull", 0), "arguments": ["changed"]}, {"provider": "codex"}
        ),
    )
    err = p.completion_error(conn, s, d, r)
    assert "증명" in err and "2판" in err


def test_lane_error_checks_format_and_policy_lanes(conn, tmp_path, monkeypatch):
    from trader.kernel.session import research_policy as p

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    (tmp_path / "on").mkdir()
    (tmp_path / "off").mkdir()
    s, d, r, _ = _cross_setup(conn, tmp_path / "on")
    assert p.lane_error(conn, s, d, r, "c") == "--lane 은 a|b 입니다"
    assert p.lane_error(conn, s, d, r, "a") is None
    assert p.lane_error(conn, s, d, r, "b") is None
    off = settings(enabled=False)
    p.start(conn, off, tmp_path / "off", SPOT_RESEARCH, now=NOW)
    assert p.lane_error(conn, off, tmp_path / "off", SPOT_RESEARCH, "b") == (
        "이 연구에는 2판이 없습니다"
    )
    assert p.lane_error(conn, off, tmp_path / "off", SPOT_RESEARCH, "a") is None


@pytest.mark.parametrize("scope", ["spot", "market", "fo"])
def test_summary_lane_error_rejects_lane_b_files_without_lane_b(conn, tmp_path, monkeypatch, scope):
    """요약 심층 방어: 판 b 파일은 정책에 판 b 가 있을 때만 요약에 들어간다."""
    from trader.kernel.session import research_policy as p

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    r = RESEARCHES[scope]
    (tmp_path / "on").mkdir()
    (tmp_path / "off").mkdir()
    s, d, _, _ = _cross_setup(conn, tmp_path / "on", scope)
    _write_claude(d, r, "b", 0, "bear")
    assert p.summary_lane_error(conn, s, d, r) is None
    off = settings(enabled=False)
    p.start(conn, off, tmp_path / "off", r, now=NOW)
    _write_claude(tmp_path / "off", r, "a", 0, "bull")
    assert p.summary_lane_error(conn, off, tmp_path / "off", r) is None  # 판 b 파일 없음
    _write_claude(tmp_path / "off", r, "b", 0, "bull")
    assert p.summary_lane_error(conn, off, tmp_path / "off", r) == "이 연구에는 2판이 없습니다"


def test_cross_codex_bull_proof_with_bear_prompt_hash_fails(conn, tmp_path, monkeypatch):
    """판 b Codex 강세 증명의 prompt_hash 가 약세 PROMPT 해시면(행·체크포인트 모두) 증명 실패."""
    from trader.kernel.session import codex
    from trader.kernel.session import research_policy as p

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    s, d, r, ctx = _cross_setup(conn, tmp_path)
    _run_cross(conn, s, d, r, ctx, _codex_fake(d))
    assert p.completion_error(conn, s, d, r) is None
    stage = r.turn_stage("b", 0, "bull")
    cp = read_checkpoint(d, stage)
    bear_hash = codex.digest(codex.PROMPTS["bear"])
    assert cp["call"]["prompt_hash"] == codex.digest(codex.PROMPTS["bull"]) != bear_hash
    conn.execute(
        "UPDATE research_calls SET prompt_hash=? WHERE id=?", (bear_hash, cp["call"]["call_id"])
    )
    write_checkpoint(d, stage, {**cp, "call": {**cp["call"], "prompt_hash": bear_hash}})
    err = p.completion_error(conn, s, d, r)
    assert err == "Codex 참여 증명 불일치: 2판 r0_bull, 새 연구로 재분석하세요"


def test_cross_claude_slot_requires_claude_provider(conn, tmp_path, monkeypatch):
    from trader.kernel.session import research_policy as p

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    s, d, r, ctx = _cross_setup(conn, tmp_path)
    _run_cross(conn, s, d, r, ctx, _codex_fake(d))
    write_checkpoint(
        d,
        r.turn_stage("b", 1, "bear"),
        checkpoint_payload(_debate_turn("bear", 1), {"provider": "codex"}),
    )
    assert p.completion_error(conn, s, d, r) == "Claude bear 공급자 증명 없음: 2판 r1"


def test_cross_write_error_enforces_provider_slots(conn, tmp_path):
    from trader.kernel.session import research_policy as p

    s, d, r, _ = _cross_setup(conn, tmp_path)
    assert p.write_error(conn, s, d, r, round_no=0, side="bull", lane="a") is None
    assert p.write_error(conn, s, d, r, round_no=0, side="bear", lane="b") is None
    assert p.write_error(conn, s, d, r, round_no=0, side="bear", lane="a") == (
        f"Codex bear는 전용 도구로만 기록할 수 있습니다: {r.cli_template}"
    )
    assert p.write_error(conn, s, d, r, round_no=0, side="bull", lane="b") == (
        f"Codex bull는 전용 도구로만 기록할 수 있습니다: {r.cli_template}"
    )
    # 판 b 반박 라운드는 판 b 직전 라운드 양측이 필요하다(판 a 와 무관)
    _write_claude(d, r, "a", 0, "bull")
    _write_claude(d, r, "a", 0, "bear")
    assert "이전 라운드" in p.write_error(conn, s, d, r, round_no=1, side="bear", lane="b")
    assert p.write_error(conn, s, d, r, round_no=1, side="bull", lane="a") is None
    # 꺼진 v2 정책은 판 a 하나 — 판 b 쓰기 거부
    off = settings(enabled=False)
    p.start(conn, off, tmp_path / "off", SPOT_RESEARCH, now=NOW)
    assert (
        p.write_error(conn, off, tmp_path / "off", SPOT_RESEARCH, round_no=0, side="bear", lane="b")
        == "이 연구에는 2판이 없습니다"
    )
    assert (
        p.write_error(conn, off, tmp_path / "off", SPOT_RESEARCH, round_no=0, side="bear", lane="a")
        is None
    )


def test_cross_write_error_lane_check_precedes_fake_enabled(conn, tmp_path, monkeypatch):
    from trader.kernel.session import research_policy as p

    monkeypatch.setenv("FAKE_CLAUDE", "1")
    on, off = settings(), settings(enabled=False)
    assert (
        p.write_error(conn, on, tmp_path, SPOT_RESEARCH, round_no=0, side="bull", lane="b") is None
    )
    assert (
        p.write_error(conn, off, tmp_path, SPOT_RESEARCH, round_no=0, side="bull", lane="b")
        == "이 연구에는 2판이 없습니다"
    )


def test_v1_policy_rejects_lane_b_and_bull_payload(conn, tmp_path):
    from trader.kernel.session import research_policy as p

    s = settings()
    p.start(conn, s, tmp_path, SPOT_RESEARCH, now=NOW, version=1)
    seed(tmp_path)
    assert (
        p.write_error(conn, s, tmp_path, SPOT_RESEARCH, round_no=0, side="bear", lane="b")
        == "이 연구에는 2판이 없습니다"
    )
    with pytest.raises(p.ResearchPolicyError, match="이 연구에서 Codex bull 턴은 없습니다"):
        p.payload(conn, s, tmp_path, SPOT_RESEARCH, 0, side="bull")
    data, _ = p.payload(conn, s, tmp_path, SPOT_RESEARCH, 0)
    assert data["side"] == "bear" and "lane" not in data


def test_cross_completion_reports_missing_lane_b(conn, tmp_path, monkeypatch):
    from trader.kernel.session import research_policy as p

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    s, d, r, ctx = _cross_setup(conn, tmp_path)
    fake = _codex_fake(d)
    from pathlib import Path

    # 판 a 만 다 채운다
    for rnd in (0, 1):
        _write_claude(d, r, "a", rnd, "bull")
        assert p.generate_turn(
            ctx, d, r, rnd, side="bear", runner=fake, bin_path=Path("/fake/codex")
        )["ok"]
    assert p.completion_error(conn, s, d, r) == "Codex 필수 토론 미완료: 2판 r0_bull"
    _write_claude(d, r, "b", 0, "bull")  # 강세 칸은 채웠지만 약세 Claude 누락
    assert p.completion_error(conn, s, d, r) == "Codex 필수 토론 미완료: 2판 r0_bear"


def _growth_turn(side, rnd, topic):
    other = "bull" if side == "bear" else "bear"
    return ok_turn(
        side,
        rnd,
        issues=[
            {
                "issue_id": f"{side}:{topic}",
                "topic": topic,
                "claim": "합성 근거",
                "evidence": ["technical.indicators"],
            }
        ],
        responses=[
            {
                "issue_id": f"{other}:{'rates' if side == 'bull' else 'growth'}",
                "response": "반박",
                "evidence": ["technical.indicators"],
            }
        ]
        if rnd
        else [],
        rebuttals=["반박"] if rnd else [],
    )


def test_lane_b_turn_error_ignores_lane_a_issues(conn, tmp_path):
    from trader.kernel.session import research_policy as p

    s, d, r, _ = _cross_setup(conn, tmp_path)
    # 판 a 의 r0 쟁점은 다른 ID(bull:valuation·bear:cycle)
    write_checkpoint(
        d, r.turn_stage("a", 0, "bull"), checkpoint_payload(_growth_turn("bull", 0, "valuation"))
    )
    write_checkpoint(
        d, r.turn_stage("a", 0, "bear"), checkpoint_payload(_growth_turn("bear", 0, "cycle"))
    )
    # 판 b r0: bull:growth / bear:rates
    write_checkpoint(
        d, r.turn_stage("b", 0, "bull"), checkpoint_payload(_growth_turn("bull", 0, "growth"))
    )
    write_checkpoint(
        d, r.turn_stage("b", 0, "bear"), checkpoint_payload(_growth_turn("bear", 0, "rates"))
    )
    lane_b_r1_bear = _growth_turn("bear", 1, "rates")
    assert p.turn_error(conn, s, d, r, lane_b_r1_bear, lane="b") is None
    # 같은 턴을 판 a 로 검사하면 판 a 쟁점과 맞지 않는다
    assert (
        p.turn_error(conn, s, d, r, lane_b_r1_bear, lane="a")
        == "자신의 이전 쟁점 ID를 유지해야 합니다"
    )


def test_payload_for_bull_reads_lane_b_previous(conn, tmp_path):
    from trader.kernel.session import research_policy as p

    s, d, r, _ = _cross_setup(conn, tmp_path)
    with pytest.raises(p.ResearchPolicyError, match="이전 라운드"):
        p.payload(conn, s, d, r, 1, side="bull")
    for side in ("bull", "bear"):
        _write_claude(d, r, "a", 0, side)
    lane_b = {
        "bull": _growth_turn("bull", 0, "growth"),
        "bear": _growth_turn("bear", 0, "rates"),
    }
    for side, turn in lane_b.items():
        write_checkpoint(d, r.turn_stage("b", 0, side), checkpoint_payload(turn))
    data, _ = p.payload(conn, s, d, r, 1, side="bull")
    assert data["side"] == "bull" and data["opponent_previous"]["side"] == "bear"
    assert data["opponent_previous"]["issues"][0]["issue_id"] == "bear:rates"
    assert data["own_previous"]["issues"][0]["issue_id"] == "bull:growth"
    assert "lane" not in data
    bear, _ = p.payload(conn, s, d, r, 1)  # 기본 side=bear → 판 a
    assert bear["side"] == "bear" and bear["own_previous"]["issues"][0]["issue_id"] == "bear:case"


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


@pytest.mark.parametrize("side", ["bull", "bear"])
def test_second_rebuttal_round_rejects_missing_or_duplicate_replies(conn, tmp_path, side):
    """실제 CLI의 r2 재답변 누락도 성공 턴으로 인정하지 않는다."""
    from trader.kernel.session import research_policy as p

    s = settings()
    s.debate_rounds = 2
    p.start(conn, s, tmp_path, SPOT_RESEARCH, now=NOW)
    seed(tmp_path)
    previous = {}
    for who, other in (("bull", "bear"), ("bear", "bull")):
        previous[who] = ok_turn(
            who,
            1,
            issues=[
                {
                    "issue_id": f"{who}:growth",
                    "topic": "성장",
                    "claim": "합성 근거의 불확실성",
                    "evidence": ["technical.indicators"],
                }
            ],
            rebuttals=["근거를 검토함"],
            responses=[
                {
                    "issue_id": f"{other}:growth",
                    "response": "성장 전망은 실적 확인이 필요함",
                    "evidence": ["technical.indicators"],
                }
            ],
        )
        write_checkpoint(tmp_path, f"20_debate_r1_{who}", checkpoint_payload(previous[who]))
    turn = {**previous[side], "round": 2, "replies": []}
    assert "재답변 coverage" in p.turn_error(conn, s, tmp_path, SPOT_RESEARCH, turn)
    reply = {
        "issue_id": f"{side}:growth",
        "response": "전망과 실적을 구분하여 확신을 낮춤",
        "evidence": ["technical.indicators"],
    }
    turn["replies"] = [reply, reply]
    assert "재답변 coverage" in p.turn_error(conn, s, tmp_path, SPOT_RESEARCH, turn)
    turn["replies"] = [reply]
    assert p.turn_error(conn, s, tmp_path, SPOT_RESEARCH, turn) is None


# --- Codex 사용량 조절·하루 호출 제한 (2026-11-08) -----------------------------


def _limits(five=None, seven=None, *, reset_in_h=3):
    resets = (NOW.replace(hour=0) + __import__("datetime").timedelta(hours=reset_in_h)).isoformat()
    win = lambda p: None if p is None else {"used_pct": p, "resets_at": resets}  # noqa: E731
    return {"five_hour": win(five), "seven_day": win(seven), "status": "ready"}


def _use_limits(monkeypatch, value):
    from trader.kernel.session import research_policy as p

    monkeypatch.setattr(p, "_read_codex_limits", lambda settings, now: value)


def _seed_calls(conn, n):
    with conn:
        for _ in range(n):
            conn.execute(
                "INSERT INTO research_calls(provider,model,policy_hash,input_hash,prompt_hash,"
                "schema_hash,status,error,counts_toward_cap,created_at) "
                "VALUES('codex',NULL,'p','i','pr','s','ok',NULL,1,?)",
                (NOW.isoformat(),),
            )


def test_codex_over_usage_makes_new_research_claude_only_and_frozen(conn, tmp_path, monkeypatch):
    from trader.kernel.session import research_policy as p

    _use_limits(monkeypatch, _limits(five=96.0))
    s = settings()
    policy = p.start(conn, s, tmp_path, SPOT_RESEARCH, now=NOW)
    assert policy["enabled"] is False and policy["bear"] == "claude"
    assert "5시간" in policy["codex_skipped"] and "96%" in policy["codex_skipped"]
    # 다시 시작(재실행)해도 처음 고정한 정책 그대로 — 사용률이 내려가도 해시 불일치가 나지 않는다
    _use_limits(monkeypatch, _limits(five=10.0))
    again = p.start(conn, s, tmp_path, SPOT_RESEARCH, now=NOW)
    assert again["enabled"] is False and again["policy_hash"] == policy["policy_hash"]
    assert p.get(conn, s, tmp_path, SPOT_RESEARCH)["enabled"] is False
    # 여유가 생기면 다음 새 연구는 다시 Codex
    fresh = p.start(conn, s, tmp_path / "next", SPOT_RESEARCH, now=NOW)
    assert fresh["enabled"] is True and fresh["bear"] == "codex"


def test_codex_throttle_off_ignores_usage_but_not_daily_cap(conn, tmp_path, monkeypatch):
    from trader.kernel.ledger.control import set_flag
    from trader.kernel.session import research_policy as p

    _use_limits(monkeypatch, _limits(seven=99.0))
    s = settings()
    set_flag(conn, p.CODEX_THROTTLE_FLAG, False)
    assert p.throttle_enabled(conn, s) == (False, "control")
    assert p.start(conn, s, tmp_path / "a", SPOT_RESEARCH, now=NOW)["enabled"] is True
    set_flag(conn, p.CODEX_CAP_FLAG, 2)
    assert p.call_cap(conn, s) == (2, "control")
    _seed_calls(conn, 2)
    skipped = p.start(conn, s, tmp_path / "b", SPOT_RESEARCH, now=NOW)
    assert skipped["enabled"] is False and "상한(2)" in skipped["codex_skipped"]


def test_codex_allowance_ignores_unread_or_reset_windows(conn, monkeypatch):
    from trader.kernel.session import research_policy as p

    s = settings()
    for value in ({"status": "unavailable"}, _limits(five=99.0, reset_in_h=-1), _limits(five=94.9)):
        _use_limits(monkeypatch, value)
        assert p.codex_allowance(conn, s, now=NOW) == (True, None), value

    def boom(settings, now):
        raise OSError("x")

    monkeypatch.setattr(p, "_read_codex_limits", boom)
    assert p.codex_allowance(conn, s, now=NOW) == (True, None)


def test_status_reports_throttle_and_effective_cap(conn, monkeypatch):
    from trader.kernel.ledger.control import set_flag
    from trader.kernel.platform import codex_bin
    from trader.kernel.session import research_policy as p

    monkeypatch.setattr(codex_bin, "find_codex", lambda *_: "/bin/codex")
    reads = []
    monkeypatch.setattr(p, "_read_codex_limits", lambda settings, now: reads.append(1) or {})
    s = settings()
    assert p.status(conn, s, now=NOW)["throttle"]["state"] == "ok"  # 관측을 안 넘기면 상한만
    st = p.status(conn, s, now=NOW, limits=_limits(five=97.0))
    assert reads == []  # status() 는 codex 를 직접 실행하지 않는다
    assert st["throttle"]["enabled"] is True and st["throttle"]["source"] == "yaml"
    assert st["throttle"]["state"] == "over" and "5시간" in st["throttle"]["reason"]
    assert st["throttle"]["hard_pct"] == 0.95
    set_flag(conn, p.CODEX_CAP_FLAG, 7)
    st = p.status(conn, s, now=NOW)
    assert st["usage"]["daily_call_cap"] == 7 and st["usage"]["cap_source"] == "control"


def test_throttle_settings_are_not_part_of_policy_hash(conn, tmp_path):
    """조절 설정(usage_throttle·hard_pct)은 시작 판단 입력일 뿐 — 바뀌어도(업데이트·yaml 수정)
    진행 중
    연구의 정책 해시가 그대로여서 다시 시작이 거부되지 않는다."""
    from trader.kernel.session import research_policy as p

    s = settings()
    assert "usage_throttle" not in p.current(conn, s)["config"]
    assert "hard_pct" not in p.current(conn, s)["config"]
    first = p.start(conn, s, tmp_path, SPOT_RESEARCH, now=NOW)
    s.llm.codex_debate.hard_pct = 0.5
    s.llm.codex_debate.usage_throttle = False
    again = p.start(conn, s, tmp_path, SPOT_RESEARCH, now=NOW)
    assert again["policy_hash"] == first["policy_hash"]


def test_completed_research_never_reads_codex_usage(conn, tmp_path, monkeypatch):
    from trader.kernel.session import research_policy as p

    reads = []
    monkeypatch.setattr(p, "_read_codex_limits", lambda settings, now: reads.append(1) or {})
    write_checkpoint(tmp_path, SPOT_RESEARCH.completed_stage, checkpoint_payload({"ok": True}))
    out = p.start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW)
    assert out["enabled"] is False and "codex_skipped" not in out
    assert reads == []  # 끝난 연구는 사용률을 읽지 않는다


def test_skip_when_remaining_calls_cannot_finish_research(conn, tmp_path, monkeypatch):
    """남은 호출이 이 연구의 bear 턴 수(rounds+1)보다 적으면 시작부터 Claude 로
    (도중 상한 실패 방지)."""
    from trader.kernel.ledger.control import set_flag
    from trader.kernel.session import research_policy as p

    _use_limits(monkeypatch, {"status": "unavailable"})
    s = settings()  # debate_rounds=0 → Codex 켜짐이면 rounds 1 → 두 판 Codex 2×2턴
    set_flag(conn, p.CODEX_CAP_FLAG, 5)
    _seed_calls(conn, 2)
    out = p.start(conn, s, tmp_path, SPOT_RESEARCH, now=NOW)
    assert out["enabled"] is False and "상한(5)" in out["codex_skipped"]
    set_flag(conn, p.CODEX_CAP_FLAG, 6)
    assert p.start(conn, s, tmp_path / "b", SPOT_RESEARCH, now=NOW)["enabled"] is True


def test_v1_skip_needs_one_call_per_round(conn, tmp_path, monkeypatch):
    """레거시 v1(약세만 Codex)은 rounds+1 회만 필요."""
    from trader.kernel.ledger.control import set_flag
    from trader.kernel.session import research_policy as p

    _use_limits(monkeypatch, {"status": "unavailable"})
    s = settings()
    set_flag(conn, p.CODEX_CAP_FLAG, 3)
    _seed_calls(conn, 2)
    out = p.start(conn, s, tmp_path, SPOT_RESEARCH, now=NOW, version=1)
    assert out["enabled"] is False and "2회 필요" in out["codex_skipped"]
    set_flag(conn, p.CODEX_CAP_FLAG, 4)
    assert p.start(conn, s, tmp_path / "b", SPOT_RESEARCH, now=NOW, version=1)["enabled"] is True


def test_stale_usage_observation_does_not_block(conn, monkeypatch):
    from trader.kernel.session import research_policy as p

    _use_limits(monkeypatch, {**_limits(five=99.0), "status": "stale"})
    assert p.codex_allowance(conn, settings(), now=NOW) == (True, None)


def test_v2_policy_has_two_lanes_when_enabled(conn, tmp_path):
    from trader.kernel.session import research_policy as rp

    p = rp.start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW)
    assert p["version"] == 2 and p["rounds"] == 1
    assert p["lanes"] == [
        {"lane": "a", "bull": "claude", "bear": "codex"},
        {"lane": "b", "bull": "codex", "bear": "claude"},
    ]
    assert (p["bull"], p["bear"]) == ("claude", "codex")
    assert rp.codex_slots(p) == [("a", "bear"), ("b", "bull")] and rp.is_cross(p)
    assert rp.provider_for(p, "b", "bull") == "codex"
    assert rp.provider_for(p, "a", "bull") == "claude"
    assert p["research"]["cross_turn_prefix"] == SPOT_RESEARCH.cross_turn_prefix


def test_v2_disabled_policy_is_single_claude_lane(conn, tmp_path):
    from trader.kernel.session import research_policy as rp

    p = rp.start(conn, settings(enabled=False), tmp_path, SPOT_RESEARCH, now=NOW)
    assert p["version"] == 2
    assert p["lanes"] == [{"lane": "a", "bull": "claude", "bear": "claude"}]
    assert not rp.is_cross(p) and rp.codex_slots(p) == []
    assert rp.provider_for(p, "b", "bull") is None


def test_stored_v1_policy_reused_without_hash_check(conn, tmp_path):
    from trader.kernel.session import research_policy as rp

    old = rp.start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW, version=1)
    assert old["version"] == 1 and "lanes" not in old
    assert "cross_turn_prefix" not in old["research"]  # v1 해시 모양 그대로
    again = rp.start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW)  # 기본 v2 로 재시작
    assert again["policy_hash"] == old["policy_hash"] and again["version"] == 1
    assert rp.lanes(again) == [{"lane": "a", "bull": "claude", "bear": "codex"}]
    assert rp.codex_slots(again) == [("a", "bear")] and not rp.is_cross(again)
    # 설정이 바뀌어도(꺼짐) v1 진행 중 연구는 해시 비교 없이 그대로
    off = rp.start(conn, settings(enabled=False), tmp_path, SPOT_RESEARCH, now=NOW)
    assert off["policy_hash"] == old["policy_hash"] and off["enabled"] is True


def test_stored_v2_policy_accepted_by_v1_mode_start(conn, tmp_path):
    """세션이 v1 모드여도 저장된 v2 연구는 저장 버전으로 비교한다(정책 변경 아님)."""
    from trader.kernel.session import research_policy as rp

    first = rp.start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW)
    again = rp.start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW, version=1)
    assert again["policy_hash"] == first["policy_hash"] and again["version"] == 2
    assert rp.is_cross(again)
    with pytest.raises(rp.ResearchPolicyError, match="정책이 변경"):
        rp.start(conn, settings(enabled=False), tmp_path, SPOT_RESEARCH, now=NOW, version=1)


def test_v1_current_is_byte_identical_to_legacy(conn):
    from trader.kernel.session import codex
    from trader.kernel.session import research_policy as rp

    s = settings()
    v1 = rp.current(conn, s, version=1)
    assert set(v1) == {
        "version", "enabled", "rounds", "bull", "bear", "config",
        "claude_models", "prompt_hash", "schema_hash",
    }
    assert v1["version"] == 1 and (v1["bull"], v1["bear"]) == ("claude", "codex")
    assert v1["rounds"] == 1 and v1["prompt_hash"] == codex.digest(codex.PROMPT)
    v2 = rp.current(conn, s)
    assert v2["prompt_hashes"] == {
        "bull": codex.digest(codex.BULL_PROMPT),
        "bear": codex.digest(codex.PROMPT),
    }
    assert {k: v for k, v in v2.items() if k not in ("version", "lanes", "prompt_hashes")} == {
        k: v for k, v in v1.items() if k != "version"
    }
    assert v2["version"] == 2


def test_cross_needs_two_calls_per_round(conn, tmp_path, monkeypatch):
    from trader.kernel.session import research_policy as rp

    _use_limits(monkeypatch, {"status": "unavailable"})
    _seed_calls(conn, 80 - 3)  # rounds 1 → 필요 4, 남은 3
    p = rp.start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW)
    assert not p["enabled"] and "4회 필요" in p["codex_skipped"]
    assert p["lanes"] == [{"lane": "a", "bull": "claude", "bear": "claude"}]
    assert p["bear"] == "claude" and not rp.is_cross(p)


def test_fake_get_and_public_policy_and_status_expose_lanes(conn, tmp_path, monkeypatch):
    from trader.kernel.platform import codex_bin
    from trader.kernel.session import research_policy as rp

    monkeypatch.setattr(codex_bin, "find_codex", lambda *_: "/bin/codex")
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    fake = rp.get(conn, settings(), tmp_path, SPOT_RESEARCH)
    assert fake["fake"] and fake["enabled"] is False and len(fake["lanes"]) == 2
    pub = rp.public_policy(conn, settings(), tmp_path, SPOT_RESEARCH)
    assert set(pub) == {"enabled", "rounds", "bull", "bear", "version", "lanes"}
    assert pub["version"] == 2 and len(pub["lanes"]) == 2
    monkeypatch.delenv("FAKE_CLAUDE")
    rp.start(conn, settings(), tmp_path / "v1", SPOT_RESEARCH, now=NOW, version=1)
    pub1 = rp.public_policy(conn, settings(), tmp_path / "v1", SPOT_RESEARCH)
    assert pub1["version"] == 1
    assert pub1["lanes"] == [{"lane": "a", "bull": "claude", "bear": "codex"}]
    st = rp.status(conn, settings(), now=NOW)
    assert st["lanes"] == rp.lanes(rp.current(conn, settings()))
    assert len(st["lanes"]) == 2 and st["roles"] == {"bull": "claude", "bear": "codex"}


def test_session_version_picks_legacy_only_for_unfinished_v1_codex(conn, tmp_path):
    from trader.kernel.session import research_policy as p

    v1_on, v1_off, fresh = tmp_path / "on", tmp_path / "off", tmp_path / "fresh"
    for d in (v1_on, v1_off, fresh):
        d.mkdir()
    p.start(conn, settings(), v1_on, SPOT_RESEARCH, now=NOW, version=1)
    p.start(conn, settings(enabled=False), v1_off, SPOT_RESEARCH, now=NOW, version=1)
    assert p.session_version(conn, [(fresh, SPOT_RESEARCH), (v1_on, SPOT_RESEARCH)]) == 1
    assert p.session_version(conn, [(v1_off, SPOT_RESEARCH)]) == 2
    assert p.session_version(conn, [(fresh, SPOT_RESEARCH)]) == 2
    assert p.session_version(conn, []) == 2
    write_checkpoint(v1_on, "30_decision", checkpoint_payload({"action": "HOLD"}))
    assert p.session_version(conn, [(v1_on, SPOT_RESEARCH)]) == 2


def test_cross_extra_s_formula_override_and_non_cross(conn, tmp_path):
    from trader.kernel.session import research_policy as p

    s = SimpleNamespace(llm=LlmSettings(codex_debate={"enabled": True}), debate_rounds=2)
    cross = p.current(conn, s)
    assert p.is_cross(cross) and cross["rounds"] == 2
    assert p.cross_extra_s(s, cross) == 3 * (180 + 120)
    frozen = {**cross, "config": {**cross["config"], "timeout_s": 300}}
    assert p.cross_extra_s(s, frozen) == 3 * (300 + 120)
    s.llm.cross_debate_extra_s = 300
    assert p.cross_extra_s(s, cross) == 300
    s.llm.cross_debate_extra_s = 0
    assert p.cross_extra_s(s, cross) == 0
    s.llm.cross_debate_extra_s = None
    assert p.cross_extra_s(s, p.current(conn, s, version=1)) == 0
    off = SimpleNamespace(llm=LlmSettings(), debate_rounds=2)
    assert p.cross_extra_s(off, p.current(conn, off)) == 0
