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
