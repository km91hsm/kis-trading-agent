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
        import hashlib

        digest = hashlib.sha256(
            json.dumps(
                data["input_context"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        receipts = [json.loads(path.read_text()) for path in (d / "input_receipts").glob("*.json")]
        assert any(row["context_hash"] == digest and row["provider"] == "codex" for row in receipts)
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
    assert {k: v for k, v in v2.items() if k not in ("version", "lanes")} == {
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
