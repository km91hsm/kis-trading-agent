"""Codex boundary: tool isolation, validation and participation are enforced by Python."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.features.spot.helpers import ok_turn
from trader.kernel.config import LlmSettings

NOW = datetime(2026, 11, 7, tzinfo=UTC)


def enabled_settings(**kw):
    return SimpleNamespace(llm=LlmSettings(codex_debate={"enabled": True, **kw}), debate_rounds=0)


def test_codex_default_off_and_invalid_limits():
    assert LlmSettings().codex_debate.enabled is False
    with pytest.raises(ValueError):
        LlmSettings(codex_debate={"timeout_s": 0})


def test_codex_bridge_sanitizes_and_isolates_child(conn, tmp_path, monkeypatch):
    from trader.kernel.session import codex

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    monkeypatch.setenv("TRADER_HOME", "/secret/trader")
    monkeypatch.setenv("KIS_APP_KEY", "do-not-inherit")
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-inherit")
    settings = enabled_settings(model="test-model")
    captured = {}

    def fake(cmd, **kw):
        captured.update(cmd=cmd, **kw)
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text(json.dumps(ok_turn("bear", 0)))
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 8}}
            ),
            "",
        )

    result = codex.run_turn(
        conn,
        settings=settings,
        policy_hash="p",
        data={
            "scope": "spot",
            "market": "KR",
            "ticker": "005930",
            "currency": "KRW",
            "asof": NOW.isoformat(),
            "round": 0,
            "side": "bear",
            "reports": [],
            "accounts": [{"secret": "should-not-pass"}],
            "path": "/secret/trader",
        },
        now=NOW,
        runner=fake,
        bin_path=Path("/fake/codex"),
    )
    assert result["data"]["side"] == "bear"
    assert result["call"]["provider"] == "codex"
    assert result["call"]["model"] == "test-model"
    cmd, env = captured["cmd"], captured["env"]
    assert "--ignore-user-config" in cmd and "--ephemeral" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert not any("dangerously" in x or "ignore-rules" in x for x in cmd)
    assert "features.shell_tool=false" in cmd and "features.plugins=false" in cmd
    assert "mcp_servers={}" in cmd and 'web_search="disabled"' in cmd
    assert not {"TRADER_HOME", "KIS_APP_KEY", "OPENAI_API_KEY"} & env.keys()
    assert "should-not-pass" not in captured["input"] and "/secret/trader" not in captured["input"]
    assert not Path(captured["cwd"]).exists()
    row = conn.execute("SELECT * FROM research_calls").fetchone()
    assert row["status"] == "ok" and row["input_tokens"] == 12 and row["output_tokens"] == 8
    assert row["input_hash"] and row["schema_hash"] and row["prompt_hash"]


@pytest.mark.parametrize("failure", ["json", "schema", "side", "exit", "timeout"])
def test_codex_failures_record_error_without_fallback(conn, monkeypatch, failure):
    from trader.kernel.session import codex

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)

    def fake(cmd, **kw):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 1)
        value = (
            "not json"
            if failure == "json"
            else json.dumps(
                {} if failure == "schema" else ok_turn("bull" if failure == "side" else "bear", 0)
            )
        )
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text(value)
        return subprocess.CompletedProcess(
            cmd, 1 if failure == "exit" else 0, "", "secret stderr must not leak"
        )

    with pytest.raises(codex.CodexError):
        codex.run_turn(
            conn,
            settings=enabled_settings(),
            policy_hash="p",
            data={
                "scope": "spot",
                "market": "KR",
                "ticker": "005930",
                "currency": "KRW",
                "asof": NOW.isoformat(),
                "round": 0,
                "side": "bear",
            },
            now=NOW,
            runner=fake,
            bin_path=Path("/fake/codex"),
        )
    row = conn.execute("SELECT * FROM research_calls").fetchone()
    assert row["status"] == "error" and "secret stderr" not in row["error"]


def test_fake_claude_never_launches_codex(conn, monkeypatch):
    from trader.kernel.session import codex

    monkeypatch.setenv("FAKE_CLAUDE", "1")

    def forbidden(*a, **kw):
        pytest.fail("FAKE_CLAUDE launched Codex")

    with pytest.raises(codex.CodexError, match="FAKE_CLAUDE"):
        codex.run_turn(
            conn, settings=enabled_settings(), policy_hash="p", data={}, now=NOW, runner=forbidden
        )
    assert conn.execute("SELECT COUNT(*) FROM research_calls").fetchone()[0] == 0


def test_codex_call_cap_reserves_before_launch(conn, monkeypatch):
    from trader.kernel.session import codex

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    data = {
        "scope": "spot",
        "market": "KR",
        "ticker": "005930",
        "currency": "KRW",
        "asof": NOW.isoformat(),
        "round": 0,
        "side": "bear",
    }

    def failed(*a, **kw):
        raise OSError("offline")

    with pytest.raises(codex.CodexError):
        codex.run_turn(
            conn,
            settings=enabled_settings(daily_call_cap=1),
            policy_hash="p",
            data=data,
            now=NOW,
            runner=failed,
            bin_path=Path("/fake/codex"),
        )
    with pytest.raises(codex.CodexError, match="상한"):
        codex.run_turn(
            conn,
            settings=enabled_settings(daily_call_cap=1),
            policy_hash="p",
            data=data,
            now=NOW,
            runner=failed,
            bin_path=Path("/fake/codex"),
        )
    rows = conn.execute(
        "SELECT status,error,counts_toward_cap FROM research_calls ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[1]["status"] == "error" and "상한" in rows[1]["error"]
    assert sum(row["counts_toward_cap"] for row in rows) == 1


def test_codex_call_cap_uses_screen_override(conn, monkeypatch):
    """설정 화면의 Codex 하루 호출 제한(control)이 yaml 상한 대신 적용된다(출발 전 거부)."""
    from trader.kernel.ledger.control import set_flag
    from trader.kernel.session import codex, research_policy

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    set_flag(conn, research_policy.CODEX_CAP_FLAG, 1)
    data = {"scope": "spot", "market": "KR", "ticker": "005930", "currency": "KRW",
            "asof": NOW.isoformat(), "round": 0, "side": "bear"}  # fmt: skip
    launched = []

    def failed(*a, **kw):
        launched.append(1)
        raise OSError("offline")

    kw = dict(settings=enabled_settings(daily_call_cap=80), policy_hash="p", data=data, now=NOW,
              runner=failed, bin_path=Path("/fake/codex"))  # fmt: skip
    with pytest.raises(codex.CodexError):
        codex.run_turn(conn, **kw)
    with pytest.raises(codex.CodexError, match="상한\\(1\\)"):
        codex.run_turn(conn, **kw)
    assert launched == [1]


def test_codex_rejects_oversized_sanitized_input_without_launch(conn, monkeypatch):
    from trader.kernel.session import codex

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    data = {
        "scope": "spot",
        "market": "KR",
        "ticker": "005930",
        "currency": "KRW",
        "asof": NOW.isoformat(),
        "round": 0,
        "side": "bear",
        "reports": [{"summary": "한" * 10000}],
    }

    def forbidden(*a, **kw):
        pytest.fail("oversized payload launched Codex")

    with pytest.raises(codex.CodexError, match="24,000"):
        codex.run_turn(
            conn,
            settings=enabled_settings(),
            policy_hash="p",
            data=data,
            now=NOW,
            runner=forbidden,
            bin_path=Path("/fake/codex"),
        )


def _side_data(side: str) -> dict:
    return {
        "scope": "spot",
        "market": "KR",
        "ticker": "005930",
        "currency": "KRW",
        "asof": NOW.isoformat(),
        "round": 0,
        "side": side,
        "reports": [],
    }


def _runner_emitting(captured: dict, side: str):
    def fake(cmd, **kw):
        captured.update(cmd=cmd, **kw)
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text(json.dumps(ok_turn(side, 0)))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return fake


def test_bear_prompt_bytes_unchanged():
    from trader.kernel.session import codex

    assert (
        codex.digest(codex.PROMPT)
        == "262b395d3c0e7b5efa63b8b13ca9c6c5e623397fe2e6fb57ad8617387d707319"
    )
    assert codex.PROMPTS["bear"] is codex.PROMPT
    assert codex.PROMPTS["bull"] is codex.BULL_PROMPT
    assert codex.BULL_PROMPT.startswith("당신은 독립적인 bull 연구자입니다.")
    assert codex.BULL_PROMPT.replace("bull 연구자", "bear 연구자", 1) == codex.PROMPT


def test_codex_bull_turn_uses_bull_prompt(conn, monkeypatch):
    from trader.kernel.session import codex

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    captured: dict = {}
    result = codex.run_turn(
        conn,
        settings=enabled_settings(),
        policy_hash="p",
        data=_side_data("bull"),
        now=NOW,
        runner=_runner_emitting(captured, "bull"),
        bin_path=Path("/fake/codex"),
    )
    assert captured["input"].startswith(codex.BULL_PROMPT + "\n")
    assert result["data"]["side"] == "bull"
    assert result["call"]["prompt_hash"] == codex.digest(codex.BULL_PROMPT)
    assert result["call"]["agent"] == "researcher-bull"
    row = conn.execute("SELECT prompt_hash, status FROM research_calls ORDER BY id DESC").fetchone()
    assert row["prompt_hash"] == codex.digest(codex.BULL_PROMPT) and row["status"] == "ok"


def test_codex_bear_turn_keeps_bear_prompt_and_agent(conn, monkeypatch):
    from trader.kernel.session import codex

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    captured: dict = {}
    result = codex.run_turn(
        conn,
        settings=enabled_settings(),
        policy_hash="p",
        data=_side_data("bear"),
        now=NOW,
        runner=_runner_emitting(captured, "bear"),
        bin_path=Path("/fake/codex"),
    )
    assert captured["input"].startswith(codex.PROMPT + "\n")
    assert result["call"]["prompt_hash"] == codex.digest(codex.PROMPT)
    assert result["call"]["agent"] == "researcher-bear"


def test_codex_rejects_side_mismatch(conn, monkeypatch):
    from trader.kernel.session import codex

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    with pytest.raises(codex.CodexError, match="Codex 턴 side/round 불일치"):
        codex.run_turn(
            conn,
            settings=enabled_settings(),
            policy_hash="p",
            data=_side_data("bull"),
            now=NOW,
            runner=_runner_emitting({}, "bear"),
            bin_path=Path("/fake/codex"),
        )
    row = conn.execute("SELECT status FROM research_calls ORDER BY id DESC").fetchone()
    assert row["status"] == "error"


def test_codex_rejects_unknown_side(conn, monkeypatch):
    from trader.kernel.session import codex

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    with pytest.raises(codex.CodexError, match="scope/market/asof/ticker/currency/round/side"):
        codex.run_turn(
            conn,
            settings=enabled_settings(),
            policy_hash="p",
            data=_side_data("trader"),
            now=NOW,
            runner=_runner_emitting({}, "bear"),
            bin_path=Path("/fake/codex"),
        )
