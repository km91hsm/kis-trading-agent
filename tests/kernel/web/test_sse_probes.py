"""커널 SSE probe 레지스트리(`KERNEL_PROBES`): usage·control·jobs·runs·notices 지문.

옛 `tests/web/test_control_sse.py` 의 커널 몫(사용률·control 커서 제외·대기열·runs 상태)."""

from __future__ import annotations

from datetime import timedelta

from tests.kernel.web.conftest import NOW, ledger_deps, make_deps
from trader.kernel.ledger.control import set_flag
from trader.kernel.web import sse


def _exec(deps, sql, args=()):
    conn = deps.conn_factory()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def test_kernel_keys_are_daemon_plus_probes_and_only_daemon_is_noisy():
    assert sse.KERNEL_SSE_KEYS == ("daemon", "usage", "control", "jobs", "runs", "notices")
    assert tuple(sse.KERNEL_PROBES) == sse.KERNEL_SSE_KEYS[1:]
    assert sse.KERNEL_NOISY_KEYS == ("daemon",)


def test_snapshot_has_every_kernel_key(project):
    snap = sse.snapshot(ledger_deps(project))
    assert set(sse.KERNEL_SSE_KEYS) <= set(snap)


def test_usage_probe_counts_rate_limit_rows(project):
    deps = ledger_deps(project)
    s0 = sse.KERNEL_PROBES["usage"](deps)
    sql = "INSERT INTO llm_rate_limits(observed_at, source, raw_json) VALUES ('x','probe','{}')"
    _exec(deps, sql)
    s1 = sse.KERNEL_PROBES["usage"](deps)
    assert s1 != s0 and s1 == "1:1"


def test_control_probe_ignores_cursor_like_keys_and_same_value(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    try:
        s0 = sse.KERNEL_PROBES["control"](deps)
        set_flag(conn, "regime:KR", {"state": "bear"})
        s1 = sse.KERNEL_PROBES["control"](deps)
        assert s1 != s0
        set_flag(conn, "regime:KR", {"state": "bear"})  # 같은 값
        set_flag(conn, "telegram_offset", 99)  # 폴링마다 바뀌는 키
        set_flag(conn, "news_trigger_at:KR", NOW.isoformat())  # 내부 커서
        set_flag(conn, "ws_session_busy:real", {"until": NOW.isoformat()})  # 60초 갱신 TTL
        assert sse.KERNEL_PROBES["control"](deps) == s1
    finally:
        conn.close()


def test_jobs_probe_sees_new_request_and_status_change(project):
    deps = ledger_deps(project)
    s0 = sse.KERNEL_PROBES["jobs"](deps)
    _exec(
        deps,
        "INSERT INTO job_requests(kind, params, status, requested_at) VALUES ('x','{}','queued',?)",
        (NOW.isoformat(),),
    )
    s1 = sse.KERNEL_PROBES["jobs"](deps)
    assert s1 != s0
    _exec(deps, "UPDATE job_requests SET status='ok'")
    assert sse.KERNEL_PROBES["jobs"](deps) != s1


def test_runs_probe_sees_status_change_without_new_rows(project):
    deps = ledger_deps(project)
    _exec(
        deps,
        "INSERT INTO runs(kind, market, started_at, status) VALUES ('analyze','KR',?,'running')",
        (NOW.isoformat(),),
    )
    s0 = sse.KERNEL_PROBES["runs"](deps)
    _exec(deps, "UPDATE runs SET status='ok', finished_at=?", ((NOW + timedelta(1)).isoformat(),))
    assert sse.KERNEL_PROBES["runs"](deps) != s0


def test_notices_probe_tracks_max_id(project):
    deps = ledger_deps(project)
    s0 = sse.KERNEL_PROBES["notices"](deps)
    _exec(
        deps,
        "INSERT INTO notices(at, kind, account_id, text, channel, ok) VALUES (?,?,?,?,?,?)",
        (NOW.isoformat(), "order", None, "알림", "telegram", 1),
    )
    assert sse.KERNEL_PROBES["notices"](deps) != s0


def test_kernel_probe_without_ledger_falls_back_to_empty(project):
    snap = sse.snapshot(make_deps(project))  # 마이그레이션 없는 원장(표 없음)
    assert all(snap[k] == "" for k in sse.KERNEL_PROBES)


def test_control_probe_sees_value_change_within_same_second(project, monkeypatch):
    """updated_at 은 1초 해상도라 같은 초 안의 두 번째 변경도 값으로 잡아야 한다(paused 토글)."""
    from trader.kernel.ledger import control

    monkeypatch.setattr(control, "now_iso", lambda: "2026-11-02T01:02:03+00:00")
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    try:
        control.set_flag(conn, "paused", True)
        s1 = sse.KERNEL_PROBES["control"](deps)
        control.set_flag(conn, "paused", False)  # 같은 updated_at, 같은 건수
        assert sse.KERNEL_PROBES["control"](deps) != s1
    finally:
        conn.close()
