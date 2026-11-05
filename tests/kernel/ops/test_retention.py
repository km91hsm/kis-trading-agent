"""보존 정리: 오래된 산출물·운영일 플래그·잡 기록·알림·제어 기록만 지운다."""

from datetime import date

from trader.kernel.ledger import control
from trader.kernel.ops import retention

TODAY = date(2026, 10, 26)
OLD = "2026-05-01T00:00:00+00:00"
NEW = "2026-10-20T00:00:00+00:00"


def test_prune_removes_only_old_items(project, conn):
    runs = project / "state" / "runs"
    (runs / "2026-05-01" / "KR").mkdir(parents=True)
    (runs / "2026-10-20" / "KR").mkdir(parents=True)
    (runs / "not-a-date").mkdir(parents=True)
    control.set_flag(conn, "reconcile:paper-main:KR:2026-05-01", {"x": 1})
    control.set_flag(conn, "reconcile:paper-main:KR:2026-10-20", {"x": 1})
    control.set_flag(conn, "paused:2026-05-01", True)
    for status, at in (("ok", OLD), ("queued", OLD), ("ok", NEW)):
        conn.execute(
            "INSERT INTO job_requests(kind, params, status, source, requested_at) "
            "VALUES ('fills_sync', '{}', ?, 'web', ?)",
            (status, at),
        )
    old_run = conn.execute(
        "INSERT INTO runs(kind, market, started_at, status) VALUES ('kr_monitor','KR',?,'error')",
        (OLD,),
    ).lastrowid
    session_run = conn.execute(
        "INSERT INTO runs(kind, market, started_at, status) VALUES ('analyze','KR',?,'ok')",
        (OLD,),
    ).lastrowid
    conn.execute(
        "INSERT INTO agent_calls(run_id, agent, model, status, created_at) "
        "VALUES (?, 'orchestrator', 'opus', 'ok', ?)",
        (session_run, OLD),
    )
    for at in (OLD, NEW):
        conn.execute(
            "INSERT INTO llm_rate_limits(observed_at, source, raw_json) "
            "VALUES (?, 'session', '{}')",
            (at,),
        )
        conn.execute(
            "INSERT INTO notices(at, kind, text, channel, ok) "
            "VALUES (?, 'info', 't', 'telegram', 1)",
            (at,),
        )
        conn.execute(
            "INSERT INTO control_log(at, channel, key, old, new) "
            "VALUES (?, 'web', 'paused', '0', '1')",
            (at,),
        )
    conn.commit()

    out = retention.prune(conn, project / "state", today=TODAY, keep_days=120)
    assert out == {
        "run_dirs": 1, "control": 1, "job_requests": 1, "runs": 1, "llm_rate_limits": 1,
        "notices": 1, "control_log": 1,
    }  # fmt: skip
    assert sorted(p.name for p in runs.iterdir()) == ["2026-10-20", "not-a-date"]
    assert control.get_flag(conn, "paused:2026-05-01") is True
    assert control.get_flag(conn, "reconcile:paper-main:KR:2026-10-20") is not None
    left = {r[0] for r in conn.execute("SELECT id FROM runs")}
    assert old_run not in left and session_run in left  # 세션 행이 있는 runs 는 운영일 집계용
    assert (
        conn.execute("SELECT COUNT(*) FROM job_requests WHERE status='queued'").fetchone()[0] == 1
    )
    for table in ("llm_rate_limits", "notices", "control_log"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    # 멱등
    again = retention.prune(conn, project / "state", today=TODAY, keep_days=120)
    assert set(again) == set(out) and not any(again.values())


def test_prune_keeps_persistent_keys_with_date_like_account_ids(project, conn):
    """계좌 id 에 날짜 모양이 있어도 영구 키는 지우지 않는다."""
    control.set_flag(conn, "paused:2024-01-01", True)
    control.set_flag(conn, "watchlist:2024-01-01:KR", ["005930"])
    control.set_flag(conn, "reconcile:paper-2026-01-01:KR:2026-01-02", {"x": 1})
    out = retention.prune(conn, project / "state", today=TODAY, keep_days=120)
    assert out["control"] == 1
    assert control.get_flag(conn, "paused:2024-01-01") is True
    assert control.get_flag(conn, "watchlist:2024-01-01:KR") == ["005930"]
    assert retention.PERSISTENT_PREFIXES == frozenset(
        {"paused", "watchlist", "telegram_offset", "memory_breaker"}
    )


def test_prune_uses_last_date_segment_as_operational_day(project, conn):
    """계좌 id 가 날짜 모양이어도 운영일은 마지막 날짜 세그먼트다."""
    keep = "sl_unrecorded:2026-01-01:KR:005930:2026-10-26"
    drop = "sl_unrecorded:2026-01-01:KR:005930:2026-01-02"
    control.set_flag(conn, keep, True)
    control.set_flag(conn, drop, True)
    control.set_flag(conn, "sl_recorded:2026-01-01:KR:005930:2026-10-25:live-auto", True)
    retention.prune(conn, project / "state", today=TODAY, keep_days=120)
    assert control.get_flag(conn, keep) is True
    assert control.get_flag(conn, drop) is None
    assert control.get_flag(conn, "sl_recorded:2026-01-01:KR:005930:2026-10-25:live-auto")
    cutoff = date(2026, 6, 28)
    assert retention.old_date("x:paper-2026-07-01:2026-01-01", cutoff) is True
    assert retention.old_date("x:2026-01-01:2026-07-01", cutoff) is False
    assert retention.old_date("x:2026-01-01x", cutoff) is False  # 통째 날짜만


def test_prune_cutoff_compares_in_utc(project, conn):
    """저장 시각은 UTC ISO — cutoff(KST 자정)도 UTC ISO 로 바꿔 비교한다(T5 M1).
    cutoff = 2026-06-28 00:00 KST = 2026-06-27T15:00Z. 그 뒤(16:00Z)는 남고 앞(14:00Z)은 지운다."""
    for at in ("2026-06-27T14:00:00+00:00", "2026-06-27T16:00:00+00:00"):
        conn.execute(
            "INSERT INTO notices(at, kind, text, channel, ok) "
            "VALUES (?, 'info', 't', 'telegram', 1)",
            (at,),
        )
    conn.commit()
    out = retention.prune(conn, project / "state", today=TODAY, keep_days=120)
    assert out["notices"] == 1
    left = [r[0] for r in conn.execute("SELECT at FROM notices")]
    assert left == ["2026-06-27T16:00:00+00:00"]
