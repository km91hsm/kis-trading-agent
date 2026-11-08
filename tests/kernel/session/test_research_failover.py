"""도중 Codex 실패 표시와 재시작 때 토론 보관 → Claude 2인 교체(스펙 §5-1)."""

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.kernel.session.test_research_policy import (
    NOW,
    RESEARCHES,
    _seed_scope,
    _write_claude,
    settings,
)
from trader.kernel.session import research_policy as rp
from trader.kernel.session.run_store import checkpoint_payload, list_checkpoints, write_checkpoint


def _failing_runner(cmd, **kw):
    return subprocess.CompletedProcess(cmd, 1, "", "boom")


def _row(conn, cols):
    return tuple(conn.execute(f"SELECT {cols} FROM research_policies").fetchone())


def _fail_once(conn, s, d, r):
    ctx = SimpleNamespace(conn=conn, settings=s, now=lambda: NOW)
    _write_claude(d, r, "a", 0, "bull")
    out = rp.generate_turn(
        ctx, d, r, 0, side="bear", runner=_failing_runner, bin_path=Path("/fake/codex")
    )
    assert out["ok"] is False and "비정상 종료" in out["error"]
    return out


def test_cross_codex_failure_marks_policy(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    s, d, r = settings(), tmp_path, RESEARCHES["spot"]
    rp.start(conn, s, d, r, now=NOW)
    _seed_scope(d, "spot")
    _fail_once(conn, s, d, r)
    row = _row(conn, "codex_failed_at, codex_failed_reason")
    assert row[0] and "비정상 종료" in row[1]


def test_v1_codex_failure_is_not_marked(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    s, d, r = settings(), tmp_path, RESEARCHES["spot"]
    rp.start(conn, s, d, r, now=NOW, version=1)
    _seed_scope(d, "spot")
    _fail_once(conn, s, d, r)
    assert _row(conn, "codex_failed_at, codex_failed_reason") == (None, None)


def test_input_assembly_error_is_not_marked(conn, tmp_path, monkeypatch):
    """입력 조립 실패(ResearchPolicyError — 직전 라운드 없음)는 표시하지 않는다."""
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    s, d, r = settings(), tmp_path, RESEARCHES["spot"]
    rp.start(conn, s, d, r, now=NOW)
    _seed_scope(d, "spot")
    ctx = SimpleNamespace(conn=conn, settings=s, now=lambda: NOW)
    out = rp.generate_turn(
        ctx, d, r, 1, side="bear", runner=_failing_runner, bin_path=Path("/fake/codex")
    )
    assert out["ok"] is False and "이전 라운드" in out["error"]
    assert _row(conn, "codex_failed_at") == (None,)


@pytest.mark.parametrize("scope", ["spot", "market", "fo"])
def test_restart_after_codex_failure_archives_and_switches_to_claude(
    conn, tmp_path, monkeypatch, scope
):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    s, d, r = settings(), tmp_path, RESEARCHES[scope]
    p1 = rp.start(conn, s, d, r, now=NOW)
    assert rp.is_cross(p1)
    _seed_scope(d, scope)
    _write_claude(d, r, "b", 0, "bear")
    write_checkpoint(d, r.summary_stage, checkpoint_payload({"x": 1}))
    _fail_once(conn, s, d, r)  # 판 a r0 bull 을 쓰고 Codex bear 실패

    p2 = rp.start(conn, s, d, r, now=NOW)
    assert not p2["enabled"] and p2["codex_skipped"].startswith("Codex 도중 실패")
    assert "비정상 종료" in p2["codex_skipped"]
    assert p2["codex_skipped"].endswith("이번 분석은 Claude 강세·약세")
    assert rp.lanes(p2) == [{"lane": "a", "bull": "claude", "bear": "claude"}]
    assert p2["bear"] == "claude" and p2["rounds"] == s.debate_rounds
    assert p2["research"]["scope"] == r.scope
    assert p2["policy_hash"] != p1["policy_hash"]
    stages = list_checkpoints(d)
    assert not any(x.startswith((r.turn_prefix, r.cross_turn_prefix)) for x in stages)
    assert r.summary_stage not in stages
    assert r.input_stage in stages
    assert any(x.startswith(r.report_prefix) for x in stages)  # 애널리스트 보존
    (archive,) = [x for x in d.iterdir() if x.is_dir() and x.name.startswith("_codex_failed_")]
    assert (archive / f"{r.turn_stage('b', 0, 'bear')}.json").is_file()
    assert (archive / f"{r.turn_stage('a', 0, 'bull')}.json").is_file()
    assert (archive / f"{r.summary_stage}.json").is_file()
    row = _row(conn, "replaced_from_hash, replaced_reason, codex_failed_at, context_hash")
    assert row[0] == p1["policy_hash"] and "비정상 종료" in row[1]
    assert row[2] is None and row[3] is None
    p3 = rp.start(conn, s, d, r, now=NOW)  # 세 번째 시작은 그대로
    assert p3["policy_hash"] == p2["policy_hash"]
    assert len([x for x in d.iterdir() if x.name.startswith("_codex_failed_")]) == 1
    # 교체된 Claude 1판 정책: Claude 약세 쓰기 허용, 판 b 거부
    assert rp.write_error(conn, s, d, r, round_no=0, side="bear") is None
    assert "2판" in rp.write_error(conn, s, d, r, round_no=0, side="bull", lane="b")


def test_archive_name_is_windows_safe_and_unique(conn, tmp_path):
    d = tmp_path / "005930"
    d.mkdir()
    a1, a2 = rp._archive_dir(d, NOW), rp._archive_dir(d, NOW)
    assert a1 != a2 and a1.is_dir() and a2.is_dir()
    names = sorted(x.name for x in d.iterdir() if x.is_dir())
    assert names == ["_codex_failed_20261107_000000", "_codex_failed_20261107_000000_2"]
    assert all(re.fullmatch(r"_codex_failed_\d{8}_\d{6}(_\d+)?", n) for n in names)
    assert all(":" not in n for n in names)


def test_policy_change_still_rejected_without_failure(conn, tmp_path, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    s, d, r = settings(), tmp_path, RESEARCHES["spot"]
    rp.start(conn, s, d, r, now=NOW)
    _seed_scope(d, "spot")
    s.llm.codex_debate.timeout_s = s.llm.codex_debate.timeout_s + 1
    with pytest.raises(rp.ResearchPolicyError, match="변경"):
        rp.start(conn, s, d, r, now=NOW)
    assert _row(conn, "replaced_from_hash") == (None,)
    assert not any(x.name.startswith("_codex_failed_") for x in d.iterdir())


def test_mark_codex_failed_writes_columns(conn, tmp_path):
    s, d, r = settings(), tmp_path, RESEARCHES["spot"]
    rp.start(conn, s, d, r, now=NOW)
    rp.mark_codex_failed(conn, d, r, reason="한도", now=NOW)
    assert _row(conn, "codex_failed_at, codex_failed_reason") == (NOW.isoformat(), "한도")
    assert rp._failure(conn, d, r) == (NOW.isoformat(), "한도")
