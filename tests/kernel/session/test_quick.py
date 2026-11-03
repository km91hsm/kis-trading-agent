"""단발 LLM 호출 quick.ask_json·probe_usage (claude 없이 러너 주입)."""

import json
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pydantic import BaseModel, Field

from trader.kernel.config import LlmSettings
from trader.kernel.registry.types import AgentDef
from trader.kernel.session import calls, quick, runner

NOW = datetime(2026, 10, 26, 2, 0, tzinfo=UTC)
GOOD = {"go": True, "reason": "돌파 확인", "confidence": 0.7}


class Verdict(BaseModel):
    go: bool
    reason: str
    confidence: float = Field(ge=0, le=1)


class _Settings:
    llm = LlmSettings()
    models: dict = {}
    claude_bin = None


@pytest.fixture(autouse=True)
def _no_fake(monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)


@pytest.fixture
def agent(tmp_path):
    md = tmp_path / "briefing-test.md"
    md.write_text("---\nmodel: haiku\n---\n판정자 지시문\n", encoding="utf-8")
    return AgentDef("briefing-test", md, None)


def _runner(result_text, *, rc=0, seen=None):
    def run(cmd, **kw):
        if seen is not None:
            seen.update(cmd=cmd, kw=kw)
        lines = [
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"unifiedWindows": {"five_hour": {"utilization": 0.4}}},
            },
            {
                "type": "result",
                "is_error": False,
                "result": result_text,
                "total_cost_usd": 0.01,
                "session_id": "s",
            },
        ]
        return CompletedProcess(cmd, rc, stdout="\n".join(json.dumps(x) for x in lines), stderr="")

    return run


def _ask(conn, agent, run, cap=60, **kw):
    return quick.ask_json(
        _Settings(),
        conn,
        agent=agent,
        payload={"ticker": "005930"},
        schema=Verdict,
        cap=cap,
        timeout_s=5,
        runner=run,
        claude_bin=Path("/x/claude"),
        now=lambda: NOW,
        **kw,
    )


def test_ask_json_parses_valid(conn, agent):
    seen = {}
    v = _ask(conn, agent, _runner("```json\n" + json.dumps(GOOD) + "\n```", seen=seen))
    assert v.go is True and v.confidence == 0.7
    cmd = seen["cmd"]
    pairs = {cmd[i]: cmd[i + 1] for i in range(1, len(cmd) - 1)}
    assert pairs["--tools"] == "" and pairs["--output-format"] == "stream-json"
    assert pairs["--model"] == "haiku" and pairs["--max-budget-usd"] == "0.2"
    assert pairs["--append-system-prompt"].startswith("판정자 지시문\n\n스키마: ")
    assert "--agents" not in cmd and json.loads(seen["kw"]["input"]) == {"ticker": "005930"}
    assert seen["kw"]["env"]["CLAUDE_CODE_RETRY_WATCHDOG"] == "1"
    row = conn.execute("SELECT agent, status, CAST(cost_usd AS REAL) FROM agent_calls").fetchone()
    assert tuple(row) == ("briefing_test", "ok", 0.01)
    assert conn.execute("SELECT five_hour_util FROM llm_rate_limits").fetchone()[0] == 0.4


def test_ask_json_timeout_returns_none(conn, agent):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)

    assert _ask(conn, agent, boom) is None
    assert conn.execute("SELECT status FROM agent_calls").fetchone()[0] == "error"


def test_ask_json_passes_cancel_and_handles_cancelled(conn, agent):
    ev = threading.Event()
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        raise runner.SessionCancelled(cmd, 5)

    assert _ask(conn, agent, run, cancel=ev) is None
    assert seen["cancel"] is ev
    row = conn.execute("SELECT status, error FROM agent_calls").fetchone()
    assert row[0] == "error" and "종료" in row[1]


def test_ask_json_schema_violation_or_text_is_none(conn, agent):
    assert _ask(conn, agent, _runner("모르겠다")) is None
    assert _ask(conn, agent, _runner(json.dumps({**GOOD, "confidence": 5}))) is None


def test_ask_json_cap_blocks(conn, agent):
    called = []

    def run(cmd, **kw):
        called.append(1)
        return _runner(json.dumps(GOOD))(cmd, **kw)

    assert _ask(conn, agent, run, cap=1) is not None
    assert _ask(conn, agent, run, cap=1) is None and len(called) == 1


def test_ask_json_not_counted_in_session_cap(conn, agent):
    _ask(conn, agent, _runner(json.dumps(GOOD)))
    assert calls.sessions_today(conn, NOW) == 0


def test_fake_mode_uses_fake_or_none(conn, agent, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    v = _ask(conn, agent, None, fake=lambda: {**GOOD, "go": False})
    assert v.go is False
    assert _ask(conn, agent, None) is None


def test_probe_usage_records_observation_outside_session_cap(conn, monkeypatch):
    seen: dict = {}
    out = quick.probe_usage(
        _Settings(), conn, runner=_runner("ok", seen=seen), claude_bin=Path("claude"),
        now=lambda: NOW,
    )  # fmt: skip
    assert out == {"ok": True}
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "haiku"
    row = conn.execute("SELECT source, five_hour_util FROM llm_rate_limits").fetchone()
    assert row["source"] == "probe" and abs(row["five_hour_util"] - 0.4) < 1e-9
    assert tuple(conn.execute("SELECT agent, status FROM agent_calls").fetchone()) == (
        "usage_probe",
        "ok",
    )
    assert calls.sessions_today(conn, NOW) == 0
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    assert quick.probe_usage(_Settings(), conn, runner=_runner("ok")) == {"skipped": "FAKE"}


def test_probe_usage_failure_is_recorded_not_raised(conn):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    out = quick.probe_usage(
        _Settings(), conn, runner=boom, claude_bin=Path("claude"), now=lambda: NOW
    )
    assert "error" in out
    assert conn.execute("SELECT status FROM agent_calls").fetchone()[0] == "error"
