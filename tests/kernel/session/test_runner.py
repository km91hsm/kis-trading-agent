"""kernel/session/runner: run_session·build_command·run_claude·stream 파싱(실 claude 미사용)."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from trader.kernel.config import ConfigError, LlmSettings
from trader.kernel.lane.types import LaneContext
from trader.kernel.platform import bundle
from trader.kernel.registry.types import AgentDef, SessionKind
from trader.kernel.session import agents as ag
from trader.kernel.session import calls, runner, usage
from trader.kernel.session.run_store import list_checkpoints, run_dir, write_checkpoint
from trader.kernel.session.runner import SessionSpec, build_command, run_session

D = date(2026, 10, 9)
T0 = datetime(2026, 10, 9, 2, 0, tzinfo=UTC)
FIX = Path(__file__).resolve().parents[2] / "fixtures" / "agents"
AGENTS = ag.by_name(
    [AgentDef("alpha", FIX / "alpha.md"), AgentDef("orchestrator", FIX / "orchestrator.md")]
)


def _kind(**over) -> SessionKind:
    base = {
        "kind": "analyze",
        "agents": ("alpha",),
        "prompt_section": "",
        "schemas": (),
        "timeout_key": "analyze",
        "runs_kind": "analyze",
        "extra_cap_key": None,
        "throttle": "hard",
        "fake": lambda ctx, spec: ["30_decision"],
    }
    base.update(over)
    return SessionKind(**base)


KIND = _kind()


def make_ctx(project, conn, *, llm=None, anonymize=False, now=lambda: T0) -> LaneContext:
    settings = SimpleNamespace(
        llm=llm or LlmSettings(usage_file=None),
        models={},
        anonymize=anonymize,
        claude_bin=None,
        debate_rounds=1,
    )
    return LaneContext(
        settings=settings,
        conn=conn,
        state=project / "state",
        now=now,
        dry_run=True,
        notifier=None,
        ports={},
    )


def spec(**over):
    base = {
        "market": "KR",
        "date": D,
        "tickers": [{"ticker": "005930", "exchange": None}],
        "dry_run": True,
    }
    base.update(over)
    return SessionSpec(**base)


def _run(sp, ctx, kind=KIND, **kw):
    kw.setdefault("claude_bin", Path("/x/claude"))
    return run_session(sp, kind, ctx, agents=AGENTS, **kw)


def _ok_result(**over):
    r = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": '{"done": true}',
        "session_id": "sess-1",
        "total_cost_usd": 0.42,
        "duration_ms": 1234,
        "num_turns": 9,
    }
    r.update(over)
    return CompletedProcess([], 0, stdout=json.dumps(r), stderr="")


@pytest.fixture(autouse=True)
def _real_mode(monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)


def test_build_command_flags(project):
    st = SimpleNamespace(models={"orchestrator": "sonnet"}, llm=LlmSettings())
    cmd = build_command(
        spec(), KIND, st, Path("/x/claude"), Path("/tmp/agents.json"), session_id="u-1",
        prompt="PROMPT",
    )  # fmt: skip
    assert cmd[:2] == [str(Path("/x/claude")), "-p"]
    assert (
        "--bare" not in cmd
        and "--max-turns" not in cmd
        and "--append-system-prompt-file" not in cmd
    )
    pairs = {cmd[i]: cmd[i + 1] for i in range(2, len(cmd) - 1)}
    assert pairs["--output-format"] == "stream-json" and pairs["--model"] == "sonnet"
    assert "--verbose" in cmd
    assert pairs["--tools"] == "Bash,Agent"
    assert pairs["--allowedTools"] == "Bash(python -m trader tool *)"
    assert pairs["--permission-mode"] == "dontAsk" and pairs["--setting-sources"] == ""
    assert pairs["--agents"] == str(Path("/tmp/agents.json"))
    assert pairs["--session-id"] == "u-1"
    assert pairs["--append-system-prompt"] == "PROMPT"
    assert float(pairs["--max-budget-usd"]) == st.llm.budget_usd
    assert "--disable-slash-commands" in cmd


def test_frozen_build_command_uses_bundle_tool_command(project, monkeypatch):
    monkeypatch.setattr(bundle, "tool_command", lambda: "trader tool")
    st = SimpleNamespace(models={}, llm=LlmSettings())
    cmd = build_command(
        spec(), KIND, st, Path("/x/claude"), Path("/tmp/a.json"), session_id="s", prompt="p"
    )
    pairs = {cmd[i]: cmd[i + 1] for i in range(2, len(cmd) - 1)}
    assert pairs["--allowedTools"] == "Bash(trader tool *)"
    assert pairs["--model"] == "sonnet"  # models 에 없으면 기본 sonnet


def test_stdin_payload_base_keys_and_stdin_extra():
    k = _kind(stdin_extra=lambda sp: {"candidates": sp.extra["candidates"]})
    sp = spec(extra={"candidates": [{"ticker": "X"}]})
    payload = json.loads(runner.stdin_payload(sp, k, T0))
    assert set(payload) == {"kind", "market", "date", "tickers", "dry_run", "asof", "candidates"}
    assert payload["candidates"] == [{"ticker": "X"}] and payload["asof"] == T0.isoformat()
    assert "candidates" not in json.loads(runner.stdin_payload(sp, KIND, T0))


def test_run_session_records_call_and_writes_agents_file(project, conn, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")  # venv bin 이 PATH 에 없는 상황을 흉내 낸다
    seen = {}

    def fake_runner(cmd, **kw):
        seen["cmd"] = cmd
        seen["kw"] = kw
        seen["agents"] = json.loads((Path(kw["cwd"]) / "agents.json").read_text(encoding="utf-8"))
        return _ok_result()

    res = _run(spec(), make_ctx(project, conn), runner=fake_runner)
    assert res.ok and res.cost_usd == 0.42 and res.session_id == "sess-1"
    kw = seen["kw"]
    assert kw["shell"] is False and kw["encoding"] == "utf-8" and kw["errors"] == "replace"
    assert kw["env"]["CLAUDE_CODE_RETRY_WATCHDOG"] == "1"
    assert kw["env"]["TRADER_DRY_RUN"] == "1"
    assert kw["env"]["TRADER_HOME"] == str(project)
    assert kw["env"]["PATH"].split(os.pathsep)[0] == str(Path(sys.executable).parent)
    assert Path(kw["cwd"]).name.startswith("trader-claude-")
    assert kw["timeout"] == LlmSettings().timeout_for("analyze")
    stdin = json.loads(kw["input"])
    assert stdin["tickers"][0]["ticker"] == "005930" and stdin["dry_run"] is True
    assert set(seen["agents"]) == {"alpha"} and seen["agents"]["alpha"]["tools"] == []
    row = conn.execute("SELECT agent, status, cost_usd, session_id FROM agent_calls").fetchone()
    assert (row["agent"], row["status"], float(row["cost_usd"]), row["session_id"]) == (
        "orchestrator",
        "ok",
        0.42,
        "sess-1",
    )
    run = conn.execute("SELECT kind, status FROM runs").fetchone()
    assert (run["kind"], run["status"]) == ("analyze", "ok")


def test_session_error_recorded_and_resumable(project, conn):
    d = run_dir(D, "KR", "005930", project / "state")
    write_checkpoint(d, "10_alpha", {"schema_version": 1, "data": {}, "call": None})
    res = _run(
        spec(),
        make_ctx(project, conn),
        runner=lambda cmd, **kw: _ok_result(is_error=True, result="budget exceeded"),
    )
    assert res.ok is False and "budget" in res.error
    assert conn.execute("SELECT status FROM agent_calls").fetchone()["status"] == "error"
    assert conn.execute("SELECT status FROM runs").fetchone()["status"] == "error"
    assert list_checkpoints(d) == ["10_alpha"]  # 보존: 재실행 시 이어간다


def test_runner_arbitrary_exception_closes_rows_and_propagates(project, conn):
    def boom(cmd, **kw):
        raise RuntimeError("러너 폭발")

    with pytest.raises(RuntimeError, match="러너 폭발"):
        _run(spec(), make_ctx(project, conn), runner=boom)
    assert conn.execute("SELECT status FROM agent_calls").fetchone()["status"] == "error"
    assert conn.execute("SELECT status FROM runs").fetchone()["status"] == "error"


def test_stdin_extra_failure_does_not_leave_running_rows(project, conn):
    def bad_extra(sp):
        return {"x": sp.extra["missing"]}

    with pytest.raises(KeyError):
        _run(
            spec(),
            make_ctx(project, conn),
            kind=_kind(stdin_extra=bad_extra),
            runner=lambda *a, **k: _ok_result(),
        )
    assert conn.execute("SELECT status FROM agent_calls").fetchone()["status"] == "error"
    assert conn.execute("SELECT status FROM runs").fetchone()["status"] == "error"


def test_session_non_json_output_is_error(project, conn):
    res = _run(
        spec(),
        make_ctx(project, conn),
        runner=lambda cmd, **kw: CompletedProcess([], 0, stdout="<html>", stderr=""),
    )
    assert res.ok is False and "JSON" in res.error


def test_daily_cap_blocks(project, conn):
    ctx = make_ctx(project, conn, llm=LlmSettings(usage_file=None, daily_session_cap=1))
    _run(spec(), ctx, runner=lambda cmd, **kw: _ok_result())
    with pytest.raises(runner.PipelineError, match="상한"):
        _run(spec(), ctx, runner=lambda cmd, **kw: _ok_result())


def test_daily_cap_uses_screen_override(project, conn):
    """설정 화면의 하루 세션 제한(control)이 yaml 대신 적용된다."""
    from trader.kernel.ledger.control import set_flag
    from trader.kernel.session import usage

    ctx = make_ctx(project, conn, llm=LlmSettings(usage_file=None, daily_session_cap=80))
    set_flag(conn, usage.SESSION_CAP_FLAG, 1)
    _run(spec(), ctx, runner=lambda cmd, **kw: _ok_result())
    with pytest.raises(runner.PipelineError, match="상한\\(1\\)"):
        _run(spec(), ctx, runner=lambda cmd, **kw: _ok_result())


def test_daily_cap_ignores_fake_sessions(project, conn, monkeypatch):
    """개발 중 FAKE 를 아무리 돌려도 실제 세션 상한·비용 집계에 들어가지 않는다."""
    ctx = make_ctx(project, conn, llm=LlmSettings(usage_file=None, daily_session_cap=1))
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    _run(spec(), ctx)
    _run(spec(), ctx)
    monkeypatch.delenv("FAKE_CLAUDE")
    assert calls.sessions_today(conn, T0) == 0
    res = _run(spec(), ctx, runner=lambda cmd, **kw: _ok_result())
    assert res.ok and calls.sessions_today(conn, T0) == 1


def test_fake_session_uses_kind_fake_and_never_spawns(project, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    got = {}

    def fake(ctx, sp):
        got["args"] = (ctx, sp)
        return ["10_a", "30_decision"]

    sp = spec()
    ctx = make_ctx(project, conn)
    res = _run(
        sp,
        ctx,
        kind=_kind(fake=fake),
        runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess 호출 금지")),
    )
    assert res.ok and res.fake and res.stages_written == ["10_a", "30_decision"]
    assert got["args"] == (ctx, sp) and res.session_id == "fake" and res.cost_usd == 0.0
    row = conn.execute("SELECT status, model, cost_usd FROM agent_calls").fetchone()
    assert (row[0], row[1], float(row[2])) == ("ok", "fake", 0.0)
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "ok"


def test_fake_failure_is_error_result(project, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")

    def boom(ctx, sp):
        raise ValueError("00_inputs 없음")

    res = _run(spec(), make_ctx(project, conn), kind=_kind(fake=boom))
    assert res.ok is False and "00_inputs" in res.error
    assert conn.execute("SELECT status FROM agent_calls").fetchone()[0] == "error"
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "error"


def test_running_row_recorded_before_session_and_duration_measured(project, conn):
    """시작 시 running 행을 먼저 쓰고 종료 시 파이썬 측 소요시간으로 갱신한다."""
    clock = [T0]

    def fake_runner(cmd, **kw):
        row = conn.execute("SELECT status FROM agent_calls").fetchone()
        assert row["status"] == "running"
        clock[0] = T0 + timedelta(seconds=250)
        return _ok_result(duration_ms=6838)

    res = _run(spec(), make_ctx(project, conn, now=lambda: clock[0]), runner=fake_runner)
    row = conn.execute("SELECT status, duration_ms FROM agent_calls").fetchone()
    assert row["status"] == "ok" and row["duration_ms"] == 250_000 and res.duration_ms == 250_000


def test_stdin_carries_asof(project, conn):
    seen = {}

    def fake_runner(cmd, **kw):
        seen.update(kw)
        return _ok_result()

    _run(spec(asof=T0), make_ctx(project, conn), runner=fake_runner)
    assert json.loads(seen["input"])["asof"] == T0.isoformat()


def test_anonymize_true_is_rejected_until_wired(project, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    with pytest.raises(ConfigError, match="anonymize"):
        _run(spec(), make_ctx(project, conn, anonymize=True))


def test_session_cwd_is_temp_and_removed(project, conn):
    """세션 cwd 는 저장소 밖 임시 디렉터리이며 세션이 끝나면 지워진다 (상위 CLAUDE.md 탐색 차단)."""
    seen = {}

    def fake_runner(cmd, **kw):
        seen["cwd"] = Path(kw["cwd"])
        seen["existed"] = seen["cwd"].is_dir() and (seen["cwd"] / "agents.json").is_file()
        return _ok_result()

    res = _run(spec(), make_ctx(project, conn), runner=fake_runner)
    assert res.ok and seen["existed"]
    assert project not in seen["cwd"].parents and not seen["cwd"].exists()


def test_runner_oserror_is_recorded_not_left_running(project, conn):
    def boom(cmd, **kw):
        raise FileNotFoundError("claude 없음")

    res = _run(spec(), make_ctx(project, conn), runner=boom)
    assert res.ok is False and "FileNotFoundError" in (res.error or "")
    assert conn.execute("SELECT status FROM agent_calls").fetchone()["status"] == "error"
    assert conn.execute("SELECT status FROM runs").fetchone()["status"] == "error"


def test_prestage_failure_closes_run_and_removes_cwd(project, conn):
    """에이전트 정의 누락 등 기동 전 단계 실패: 예외는 그대로 올리되 runs 를 error 로 닫고
    임시 디렉터리를 지운다."""
    before = set(Path(tempfile.gettempdir()).glob("trader-claude-*"))
    with pytest.raises(ConfigError):
        run_session(spec(), KIND, make_ctx(project, conn), agents={}, claude_bin=Path("/x/claude"))
    row = conn.execute("SELECT status, error FROM runs").fetchone()
    assert row["status"] == "error" and row["error"]
    assert conn.execute("SELECT COUNT(*) FROM agent_calls").fetchone()[0] == 0
    assert set(Path(tempfile.gettempdir()).glob("trader-claude-*")) == before


def test_cap_hit_inside_start_call_raises_pipeline_error(project, conn, monkeypatch):
    """start_call 이 상한으로 None 을 돌려주면(경합) PipelineError 이고 runs 는 error 로 닫힌다."""
    monkeypatch.setattr(calls, "start_call", lambda *a, **k: None)
    with pytest.raises(runner.PipelineError, match="상한"):
        _run(spec(), make_ctx(project, conn), runner=lambda *a, **k: _ok_result())
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "error"


def test_extra_cap_key_limits_by_runs_kind(project, conn):
    class Llm(LlmSettings):
        fo_cap: int = 1

    ctx = make_ctx(project, conn, llm=Llm(usage_file=None))
    k = _kind(runs_kind="fo", extra_cap_key="fo_cap")
    _run(spec(), ctx, kind=k, runner=lambda cmd, **kw: _ok_result())
    with pytest.raises(runner.PipelineError, match="fo 세션 상한"):
        _run(spec(), ctx, kind=k, runner=lambda cmd, **kw: _ok_result())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 프로세스 그룹 전용")
def test_default_runner_kills_process_group_on_timeout(tmp_path):
    marker = tmp_path / "grandchild.pid"
    child = (
        "import subprocess, sys, time, pathlib; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(60)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run_claude(
            [sys.executable, "-c", child], input="", timeout=2, cwd=str(tmp_path),
            encoding="utf-8",
        )  # fmt: skip
    pid = int(marker.read_text(encoding="utf-8"))
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        pytest.fail("손자 프로세스가 살아 있다 (프로세스 그룹 kill 안 됨)")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 프로세스 그룹 전용")
def test_default_runner_cancel_event_terminates_group_gracefully(tmp_path):
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    t0 = time.monotonic()
    with pytest.raises(runner.SessionCancelled):
        runner.run_claude(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            input="", timeout=30, cancel=cancel, cwd=str(tmp_path), encoding="utf-8",
        )  # fmt: skip
    assert time.monotonic() - t0 < 10


def test_session_records_num_turns(project, conn):
    res = _run(spec(), make_ctx(project, conn), runner=lambda cmd, **kw: _ok_result(num_turns=7))
    assert res.ok and res.num_turns == 7
    row = conn.execute("SELECT num_turns FROM agent_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert row["num_turns"] == 7


def test_session_env_path_starts_with_tool_bin_dir(project, conn, monkeypatch):
    monkeypatch.setattr(bundle, "tool_bin_dir", lambda: Path("/bundle/dir"))
    seen = {}

    def fake_runner(cmd, **kw):
        seen["path"] = kw["env"]["PATH"]
        return _ok_result()

    _run(spec(), make_ctx(project, conn), runner=fake_runner)
    assert seen["path"].split(os.pathsep)[0] == str(Path("/bundle/dir"))


def test_run_session_passes_cancel_to_runner_and_reports_cancelled(project, conn):
    seen = {}

    def fake_runner(cmd, **kw):
        seen["cancel"] = kw.get("cancel")
        raise runner.SessionCancelled(cmd, 0)

    ev = threading.Event()
    res = _run(spec(), make_ctx(project, conn), runner=fake_runner, cancel=ev)
    assert seen["cancel"] is ev
    assert not res.ok and "종료 요청" in (res.error or "")
    assert conn.execute("SELECT status FROM agent_calls").fetchone()["status"] == "error"
    assert issubclass(runner.SessionCancelled, subprocess.TimeoutExpired)


# --- stream-json 결과 + rate_limit_event --------------------------------------------------------
_RATE = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed",
        "unifiedWindows": {
            "five_hour": {"utilization": 0.31, "resetsAt": 1792990000},
            "seven_day": {"utilization": 0.72, "resetsAt": 1793300000},
        },
    },
}


def _stream(*events) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_parse_stream_result_and_rate():
    out = _stream(
        {"type": "system", "subtype": "init"},
        _RATE,
        {"type": "assistant"},
        json.loads(_ok_result().stdout),
    )
    data, rate = runner.parse_stream(out)
    assert data["total_cost_usd"] == 0.42 and data["num_turns"] == 9
    assert rate["unifiedWindows"]["five_hour"]["utilization"] == 0.31


def test_parse_stream_single_json_object_still_ok():
    data, rate = runner.parse_stream(_ok_result().stdout)
    assert data["session_id"] == "sess-1" and rate is None


def test_parse_stream_without_result_line():
    with pytest.raises(runner.PipelineError):
        runner.parse_stream(_stream({"type": "system"}, _RATE))
    with pytest.raises(runner.PipelineError):
        runner.parse_stream("not json\n")


def test_parse_stream_without_rate_event():
    data, rate = runner.parse_stream(_stream({"type": "system"}, json.loads(_ok_result().stdout)))
    assert rate is None and data["is_error"] is False


def test_run_session_records_rate_limit(project, conn):
    def fake_runner(cmd, **kw):
        return CompletedProcess(
            [], 0, stdout=_stream(_RATE, json.loads(_ok_result().stdout)), stderr=""
        )

    assert _run(spec(), make_ctx(project, conn), runner=fake_runner).ok
    row = conn.execute(
        "SELECT source, five_hour_util, seven_day_util FROM llm_rate_limits"
    ).fetchone()
    assert tuple(row) == ("session", 0.31, 0.72)


def test_run_session_no_result_line_is_error_not_exception(project, conn):
    def fake_runner(cmd, **kw):
        return CompletedProcess([], 1, stdout=_stream(_RATE), stderr="killed")

    res = _run(spec(), make_ctx(project, conn), runner=fake_runner)
    assert not res.ok
    assert conn.execute("SELECT status FROM agent_calls").fetchone()[0] == "error"


def _throttle(conn):
    usage.record_rate_limit(
        conn,
        {"unifiedWindows": {"five_hour": {"utilization": 0.9}}},
        now=datetime.now(UTC),
    )


def test_run_session_raises_when_throttled(project, conn):
    _throttle(conn)
    ctx = make_ctx(project, conn, now=lambda: datetime.now(UTC))
    with pytest.raises(runner.PipelineError, match="사용률"):
        _run(spec(), ctx, runner=lambda *a, **k: None)
    assert conn.execute("SELECT COUNT(*) FROM agent_calls").fetchone()[0] == 0


def test_throttle_raises_throttle_blocked_and_timeout_by_kind(project, conn):
    """감속 차단은 ThrottleBlocked(PipelineError 하위). 타임아웃은 kind.timeout_key 로 고른다."""
    assert issubclass(runner.ThrottleBlocked, runner.PipelineError)
    _throttle(conn)
    ctx = make_ctx(project, conn, now=lambda: datetime.now(UTC))
    with pytest.raises(runner.ThrottleBlocked):
        _run(spec(), ctx)
    llm = LlmSettings()
    assert llm.timeout_for("analyze") == 900 and llm.timeout_for("screen") == 300
    assert llm.timeout_for("resume") == 600 and llm.timeout_for("unknown") == llm.timeout_s
    # soft 등급(throttle="soft") 이면 90% 에서도 hard 만 막는다 → 80~95% 구간에서 통과
    conn.execute("DELETE FROM llm_rate_limits")
    usage.record_rate_limit(
        conn, {"unifiedWindows": {"five_hour": {"utilization": 0.85}}}, now=datetime.now(UTC)
    )
    conn.commit()
    seen = {}

    def fake_runner(cmd, **kw):
        seen["timeout"] = kw["timeout"]
        return _ok_result()

    soft = _kind(kind="resume", timeout_key="resume", runs_kind="monitor", throttle="soft")
    assert _run(spec(), ctx, kind=soft, runner=fake_runner).ok
    assert seen["timeout"] == 600
    with pytest.raises(runner.ThrottleBlocked):
        _run(spec(), ctx, kind=_kind(), runner=fake_runner)


def _seed_two_timeouts(conn):
    for _ in range(2):
        conn.execute(
            "INSERT INTO agent_calls(ticker, market, agent, model, status, error, created_at) "
            "VALUES ('005930', 'KR', 'orchestrator', 'sonnet', 'error', '세션 타임아웃 (600s)', ?)",
            (datetime(2026, 10, 9, 2, tzinfo=UTC).isoformat(),),
        )
    conn.commit()


def test_same_ticker_not_retried_after_two_timeouts_today(project, conn):
    _seed_two_timeouts(conn)
    later = datetime(2026, 10, 9, 3, tzinfo=UTC)
    with pytest.raises(runner.PipelineError, match="타임아웃"):
        _run(spec(), make_ctx(project, conn, now=lambda: later), kind=_kind(retry_guard=True))


def test_timeout_guard_follows_kind_retry_guard_not_kind_name(project, conn):
    """Ruling 23: 반복 타임아웃 차단은 `SessionKind.retry_guard` 선언으로만 켜진다(이름 무관)."""
    _seed_two_timeouts(conn)
    later = datetime(2026, 10, 9, 3, tzinfo=UTC)
    ctx = make_ctx(project, conn, now=lambda: later)
    with pytest.raises(runner.PipelineError, match="타임아웃"):
        _run(spec(), ctx, kind=_kind(kind="screen", retry_guard=True))
    # 이름이 analyze 여도 retry_guard=False 면 차단하지 않고 세션을 띄운다
    res = _run(spec(), ctx, kind=_kind(retry_guard=False), runner=lambda cmd, **kw: _ok_result())
    assert res.ok


def test_codex_policy_missing_turns_fail_even_when_claude_exits_success(project, conn, monkeypatch):
    from trader.features.spot.session_kinds import AGENTS as SPOT_AGENTS
    from trader.features.spot.session_kinds import ANALYZE

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    from trader.features.spot.research import RESEARCH
    from trader.kernel.session import research_policy
    from trader.kernel.session.run_store import run_dir

    ctx = make_ctx(project, conn, llm=LlmSettings(codex_debate={"enabled": True}))
    # 업데이트 전에 시작된 v1(Codex 약세) 연구 — 세션은 레거시 2인 모드로 돈다
    research_policy.start(
        conn, ctx.settings, run_dir(D, "KR", "005930", ctx.state), RESEARCH, now=T0, version=1
    )
    captured = {}

    def fake(cmd, **kw):
        captured["prompt"] = cmd[cmd.index("--append-system-prompt") + 1]
        captured["agents"] = json.loads(
            Path(cmd[cmd.index("--agents") + 1]).read_text(encoding="utf-8")
        )
        return _ok_result()

    result = run_session(
        spec(),
        ANALYZE,
        ctx,
        agents=ag.by_name(SPOT_AGENTS),
        runner=fake,
        claude_bin=Path("/fake/claude"),
    )
    assert not result.ok and "Codex" in result.error
    assert "codex-turn" in captured["prompt"]
    assert "researcher-bear" not in captured["agents"]
    assert conn.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0] == "error"


# v1(레거시) 런타임 절 — 업데이트 전과 바이트 단위로 같아야 한다(판정 F4)
LEGACY_SECTION = (
    "\n# 고정된 Codex 토론 정책 (기존 흐름보다 우선)\n"
    "bull은 선언된 Claude 에이전트, bear는 Codex 전용 도구다. Claude bear를 쓰지 않는다. "
    "r0은 양측 독립 의견. r1부터 고정 debate_rounds까지 양측이 상대의 이전 라운드를 반박해야 "
    "한다. 각 반박 턴 rebuttals는 비우지 않는다. 각 공급자는 자기 issues 1~5개(issue_id는 "
    "bull:/bear: 고유ID, topic,claim,evidence=[리포트 source_id])를 제시한다. r0 "
    "responses/replies는 빈 목록. r1+는 자신의 쟁점ID를 유지하고 상대 직전 issues의 모든 ID에 "
    "responses(issue_id,response,evidence)로 반론한다. r2+는 상대 직전 responses가 지적한 "
    "자신의 쟁점 전부에 replies로 재답변한다. WTI/DXY/미국10년금리 변화가 언제나 주가악재라는 "
    "고정규칙은 금지한다. 성장/인플레/공급원인, 기대반영, 업종별 상반효과와 반례를 검토한다. "
    "애널리스트 합의라도 토론을 생략하지 않는다. Codex 오류/상한/미완료면 중단하고 오류를 "
    "보고한다. 재개 세션의 기존 결정은 원래 정책을 유지한다.\n"
    f"- spot bull: researcher-bull; bear: {bundle.tool_command()} checkpoint codex-turn "
    "--ticker <ticker> --market <market> --date <date> --round <n>\n"
    "전용 도구는 입력 묶음을 Python에서 조립하고 JSON검증 후 저장한다. 각 라운드의 bull과 "
    "bear 완료 후 다음 라운드, 마지막에 summarize를 실행한다.\n"
)


def _capture_session(project, conn, *, llm, kind=None, agents=None, rounds=1, before=None):
    """실제 claude 없이 run_session 을 돌려 프롬프트·에이전트·제한 시간·env 를 잡는다."""
    from trader.features.spot.session_kinds import AGENTS as SPOT_AGENTS
    from trader.features.spot.session_kinds import ANALYZE

    ctx = make_ctx(project, conn, llm=llm)
    ctx.settings.debate_rounds = rounds
    if before is not None:
        before(ctx)
    captured = {}

    def fake(cmd, **kw):
        captured["prompt"] = cmd[cmd.index("--append-system-prompt") + 1]
        captured["agents"] = json.loads(
            Path(cmd[cmd.index("--agents") + 1]).read_text(encoding="utf-8")
        )
        captured["timeout"] = kw["timeout"]
        captured["env"] = kw["env"]
        return _ok_result()

    result = run_session(
        spec(),
        kind or ANALYZE,
        ctx,
        agents=agents or ag.by_name(SPOT_AGENTS),
        runner=fake,
        claude_bin=Path("/fake/claude"),
    )
    return result, captured


def _codex_on(**over):
    return LlmSettings(codex_debate={"enabled": True}, usage_file=None, **over)


def test_cross_session_keeps_bear_agent_and_extends_timeout(project, conn):
    result, captured = _capture_session(project, conn, llm=_codex_on())
    assert "researcher-bear" in captured["agents"]
    assert "researcher-bull" in captured["agents"]
    for s in ("1판", "2판", "--lane b", "--side bull", "--side bear", "codex-turn", "20_debatex_r"):
        assert s in captured["prompt"], s
    assert "Claude bear를 쓰지 않는다" not in captured["prompt"]
    assert "Codex 오류/상한/미완료면 중단하고 오류를 보고한다" in captured["prompt"]
    # 기본 연장 = (라운드 1 + 1) × (Codex 180 + 120) = 600
    assert captured["timeout"] == 900 + 600
    assert captured["env"]["BASH_DEFAULT_TIMEOUT_MS"] == "240000"
    # 최대치는 Claude Code 기본 최대(600000) 아래로 낮추지 않는다
    assert captured["env"]["BASH_MAX_TIMEOUT_MS"] == "600000"
    assert not result.ok and "Codex" in result.error


def test_market_cross_timeout_is_1200(project, conn):
    from trader.features.regime.session_kinds import MARKET, agents_for_runner

    result, captured = _capture_session(
        project, conn, llm=_codex_on(), kind=MARKET, agents=agents_for_runner()
    )
    assert captured["timeout"] == 600 + 600
    assert "market-researcher-bear" in captured["agents"]
    assert "_market_debatex_r" in captured["prompt"]
    assert not result.ok and "Codex" in result.error


def test_cross_extra_override(project, conn):
    _, captured = _capture_session(project, conn, llm=_codex_on(cross_debate_extra_s=300))
    assert captured["timeout"] == 900 + 300


def test_cross_extra_rounds_two(project, conn):
    _, captured = _capture_session(project, conn, llm=_codex_on(), rounds=2)
    assert captured["timeout"] == 900 + 900


def test_cross_bash_timeout_follows_codex_timeout(project, conn):
    llm = LlmSettings(codex_debate={"enabled": True, "timeout_s": 600}, usage_file=None)
    _, captured = _capture_session(project, conn, llm=llm)
    assert captured["env"]["BASH_DEFAULT_TIMEOUT_MS"] == "660000"
    assert captured["env"]["BASH_MAX_TIMEOUT_MS"] == "660000"
    assert captured["timeout"] == 900 + 2 * (600 + 120)


def test_disabled_codex_session_has_no_extra(project, conn, monkeypatch):
    monkeypatch.delenv("BASH_DEFAULT_TIMEOUT_MS", raising=False)
    _, captured = _capture_session(project, conn, llm=LlmSettings(usage_file=None))
    assert captured["timeout"] == 900
    # Codex 연구가 없는 세션은 Claude Code 기본 Bash 제한(2분)을 그대로 둔다
    assert "BASH_DEFAULT_TIMEOUT_MS" not in captured["env"]
    # 최대치는 Claude Code 기본 최대(600000) 아래로 낮추지 않는다
    assert captured["env"]["BASH_MAX_TIMEOUT_MS"] == "600000"
    assert "researcher-bear" in captured["agents"]
    assert "codex-turn" not in captured["prompt"]


def test_session_with_v1_codex_research_runs_legacy_mode(project, conn):
    from trader.features.spot.research import RESEARCH
    from trader.kernel.session import research_policy
    from trader.kernel.session.run_store import run_dir

    def before(ctx):
        research_policy.start(
            conn, ctx.settings, run_dir(D, "KR", "005930", ctx.state), RESEARCH, now=T0, version=1
        )

    result, captured = _capture_session(project, conn, llm=_codex_on(), before=before)
    assert "researcher-bear" not in captured["agents"]
    assert "Claude bear를 쓰지 않는다" in captured["prompt"]
    # md 의 조건부 안내(「교차 토론이면」)는 남지만 런타임 절의 2판 지시는 붙지 않는다
    assert "2판 Claude 턴" not in captured["prompt"]
    # md 의 `--lane b` 지시는 조건 문구와 함께여야 한다(조건이 사라지면 레거시에서도 2판을 돈다)
    assert (
        "Codex 교차 토론이면(런타임 절이 알린다) 2판 묶음도 같은 bundle 명령에 `--lane b`"
    ) in captured["prompt"]
    assert captured["timeout"] == 900
    assert captured["env"]["BASH_DEFAULT_TIMEOUT_MS"] == "240000"
    assert not result.ok and "Codex" in result.error


def test_cross_session_timeout_message_shows_extended_limit(project, conn):
    """교차 세션 타임아웃 문구에는 연장된 제한 시간(900 + 600)이 나온다."""
    from trader.features.spot.session_kinds import AGENTS as SPOT_AGENTS
    from trader.features.spot.session_kinds import ANALYZE

    ctx = make_ctx(project, conn, llm=_codex_on())
    ctx.settings.debate_rounds = 1

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    result = run_session(
        spec(), ANALYZE, ctx, agents=ag.by_name(SPOT_AGENTS), runner=timeout,
        claude_bin=Path("/fake/claude"),
    )  # fmt: skip
    assert not result.ok and result.error == "세션 타임아웃 (1500s)"


def _cross_policy(timeout_s: int) -> dict:
    lane_a = {"lane": "a", "bull": "claude", "bear": "codex"}
    lane_b = {"lane": "b", "bull": "codex", "bear": "claude"}
    return {
        "version": 2, "enabled": True, "lanes": [lane_a, lane_b],
        "config": {"timeout_s": timeout_s},
    }  # fmt: skip


def test_cross_section_codex_timeout_wording():
    """교차 연구가 여럿이면 「각 최대 N초」는 가장 긴 연구 기준임을 밝힌다."""
    from trader.features.spot.research import RESEARCH

    one = runner.codex_section([(Path("x"), RESEARCH, _cross_policy(180))], cross=True)
    assert "(각 최대 180초)" in one and "가장 긴" not in one
    many = runner.codex_section(
        [(Path("x"), RESEARCH, _cross_policy(180)), (Path("y"), RESEARCH, _cross_policy(600))],
        cross=True,
    )
    assert "(가장 긴 연구 기준 각 최대 600초)" in many


def test_legacy_section_text_is_unchanged():
    from trader.features.spot.research import RESEARCH

    text = runner.codex_section([(Path("x"), RESEARCH, {"enabled": True})], cross=False)
    assert text == LEGACY_SECTION


def test_mixed_session_cross_section_follows_each_research_lanes():
    """교차 연구와 1판 연구가 섞인 세션: 연구마다 research_policy.lanes 를 따르라는 문장."""
    from trader.features.spot.research import RESEARCH

    lane_a = {"lane": "a", "bull": "claude", "bear": "codex"}
    lane_b = {"lane": "b", "bull": "codex", "bear": "claude"}
    cfg = {"timeout_s": 180}
    cross_p = {"version": 2, "enabled": True, "lanes": [lane_a, lane_b], "config": cfg}
    single_p = {"version": 2, "enabled": False, "lanes": [lane_a], "config": cfg}
    text = runner.codex_section(
        [(Path("x"), RESEARCH, cross_p), (Path("y"), RESEARCH, single_p)], cross=True
    )
    assert (
        "연구마다 researcher 묶음의 `research_policy.lanes` 를 따른다. 판이 하나뿐인 연구는 "
        "1판(정책대로)만 진행하고, 그 연구의 2판 거부는 오류로 보고하되 세션을 중단하지 않는다."
    ) in text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 부모 SIGTERM 전파")
def test_nested_codex_group_is_terminated_when_tool_parent_receives_sigterm(tmp_path):
    import signal

    marker = tmp_path / "codex.pid"
    child = (
        "import os,time,pathlib; pathlib.Path("
        + repr(str(marker))
        + ").write_text(str(os.getpid())); time.sleep(60)"
    )
    script = (
        "from trader.kernel.session.runner import run_claude; "
        "import sys; run_claude([sys.executable,'-c',"
        + repr(child)
        + "],input='',timeout=30,encoding='utf-8',terminate_on_parent_signal=True)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", script],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline and parent.poll() is None:
            time.sleep(0.05)
        assert marker.exists(), "nested Codex process must start"
        child_pid = int(marker.read_text())
        os.killpg(parent.pid, signal.SIGTERM)
        parent.wait(timeout=5)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("nested Codex child survived tool cancellation")
    finally:
        if parent.poll() is None:
            os.killpg(parent.pid, signal.SIGKILL)
        if child_pid:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
