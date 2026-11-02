"""calls: 세션 행(agent_calls) 시작·종료와 오늘 세션 수·상한."""

from datetime import UTC, datetime, timedelta

from trader.kernel.ledger import runs
from trader.kernel.session import calls

# KST 2026-10-23 12:00 → KST 오늘 00:00 = UTC 2026-10-22T15:00
NOW = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)


def _row(conn, agent, model, when, run_id=None):
    conn.execute(
        "INSERT INTO agent_calls(run_id, agent, model, status, created_at) "
        "VALUES (?, ?, ?, 'ok', ?)",
        (run_id, agent, model, when.isoformat()),
    )
    conn.commit()


def test_sessions_today_counts_orchestrator_non_fake_since_kst_midnight(conn):
    _row(conn, "orchestrator", "sonnet", NOW - timedelta(hours=1))
    _row(conn, "orchestrator", None, NOW - timedelta(hours=2))
    _row(conn, "orchestrator", "fake", NOW - timedelta(hours=1))
    _row(conn, "other", "sonnet", NOW - timedelta(hours=1))
    _row(conn, "orchestrator", "sonnet", datetime(2026, 10, 22, 14, 59, tzinfo=UTC))  # 어제
    assert calls.sessions_today(conn, NOW) == 2
    assert calls.calls_today(conn, "other", NOW) == 1
    assert calls.calls_today(conn, "orchestrator", NOW) == 2


def test_sessions_today_by_kind_joins_runs(conn):
    fo = runs.start(conn, "fo")
    an = runs.start(conn, "analyze")
    _row(conn, "orchestrator", "sonnet", NOW, fo)
    _row(conn, "orchestrator", "sonnet", NOW, fo)
    _row(conn, "orchestrator", "sonnet", NOW, an)
    assert calls.sessions_today(conn, NOW) == 3
    assert calls.sessions_today(conn, NOW, kind="fo") == 2
    assert calls.sessions_today(conn, NOW, kind="analyze") == 1


def _start(conn, run_id=None, **kw):
    return calls.start_call(
        conn, run_id, agent=calls.ORCHESTRATOR, model=kw.pop("model", "sonnet"),
        ticker="005930,000660", market="KR", session_id="s1", now=NOW, **kw,
    )  # fmt: skip


def test_start_call_inserts_running_and_respects_cap(conn):
    cid = _start(conn, cap=1)
    row = conn.execute("SELECT * FROM agent_calls WHERE id=?", (cid,)).fetchone()
    assert row["status"] == "running" and row["ticker"] == "005930,000660"
    assert row["agent"] == "orchestrator" and row["created_at"] == NOW.isoformat()
    assert _start(conn, cap=1) is None
    assert conn.execute("SELECT COUNT(*) FROM agent_calls").fetchone()[0] == 1
    assert not conn.in_transaction


def test_start_call_cap_ignored_for_fake(conn):
    _row(conn, "orchestrator", "sonnet", NOW)
    assert _start(conn, model="fake", cap=1, extra_cap=("fo", 0)) is not None
    assert _start(conn, cap=1) is None


def test_start_call_extra_cap_counts_by_runs_kind(conn):
    fo = runs.start(conn, "fo")
    an = runs.start(conn, "analyze")
    assert _start(conn, fo, extra_cap=("fo", 1)) is not None
    assert _start(conn, fo, extra_cap=("fo", 1)) is None
    assert _start(conn, an, extra_cap=("analyze", 1)) is not None


def test_finish_call_updates_status_cost_turns(conn):
    cid = _start(conn)
    calls.finish_call(
        conn, cid, status="ok", session_id="real", cost=0.25, duration_ms=1500, num_turns=7
    )
    row = conn.execute("SELECT * FROM agent_calls WHERE id=?", (cid,)).fetchone()
    assert (row["status"], row["session_id"], row["duration_ms"], row["num_turns"]) == (
        "ok", "real", 1500, 7,
    )  # fmt: skip
    assert float(row["cost_usd"]) == 0.25
    cid2 = _start(conn)
    calls.finish_call(conn, cid2, status="error", error="boom")
    row = conn.execute("SELECT * FROM agent_calls WHERE id=?", (cid2,)).fetchone()
    assert row["session_id"] == "s1" and row["error"] == "boom"
