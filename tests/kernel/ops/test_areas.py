"""커널 내장 영역 jobs·llm·orders 판정."""

import json
import os
from datetime import timedelta
from types import SimpleNamespace

from trader.kernel.broker import rate_store
from trader.kernel.daemon import heartbeat
from trader.kernel.ops import areas

from .conftest import NOW, FakeAccounts, account


def _keys(items):
    return {i.key: i for i in items}


def _alive(state, updated_at, jobs=None):
    (state / heartbeat.LOCK_NAME).write_text(
        json.dumps({"pid": os.getpid(), "started_at": (NOW - timedelta(hours=1)).isoformat()}),
        encoding="utf-8",
    )
    (state / heartbeat.FILE_NAME).write_text(
        json.dumps({"pid": os.getpid(), "updated_at": updated_at.isoformat(), "jobs": jobs or {}}),
        encoding="utf-8",
    )


def _run_error(conn, n):
    for i in range(n):
        conn.execute(
            "INSERT INTO runs(kind, started_at, status) VALUES (?, ?, 'error')",
            (f"k{i}", (NOW - timedelta(hours=1, minutes=i)).isoformat()),
        )
    conn.commit()


def test_jobs_area_daemon_dead_stale_missed_errors(conn, make_ctx, project):
    state = project / "state"
    got = _keys(areas.check_jobs(make_ctx()))
    assert got["jobs.daemon"].level == "critical" and got["jobs.daemon"].link == "/system#daemon"

    _alive(state, NOW - timedelta(minutes=11))
    got = _keys(areas.check_jobs(make_ctx()))
    assert "jobs.daemon" not in got and got["jobs.stale"].level == "critical"
    assert "11분" in got["jobs.stale"].text
    assert got["jobs.stale"].link == "/system#daemon"

    sched = [
        SimpleNamespace(id=j, kind=j, market="KR", cron=None, interval_s=60)
        for j in ("kr_scan", "kr_monitor", "us_scan")
    ]
    hb_jobs = {"kr_scan": {"last_status": "missed"}, "us_scan": {"last_status": "missed"},
               "kr_monitor": {"last_status": "ok"}}  # fmt: skip
    _alive(state, NOW - timedelta(seconds=30), hb_jobs)
    got = _keys(areas.check_jobs(make_ctx(jobs=sched)))
    assert "jobs.stale" not in got and "jobs.daemon" not in got
    assert got["jobs.missed"].level == "warning" and got["jobs.missed"].count == 2
    assert "kr_scan" in got["jobs.missed"].text and "jobs.errors" not in got
    assert got["jobs.missed"].link == "/system#daemon"

    _run_error(conn, 2)
    got = _keys(areas.check_jobs(make_ctx(jobs=sched)))
    assert got["jobs.errors"].level == "watch" and got["jobs.errors"].count == 2
    assert got["jobs.errors"].link == "/system#jobs"
    _run_error(conn, 1)
    conn.execute(  # 24시간 밖 오류는 세지 않는다
        "INSERT INTO runs(kind, started_at, status) VALUES ('old', ?, 'error')",
        ((NOW - timedelta(hours=25)).isoformat(),),
    )
    got = _keys(areas.check_jobs(make_ctx(jobs=sched)))
    assert got["jobs.errors"].level == "warning" and got["jobs.errors"].count == 3


def _sessions(conn, n, *, status="ok", model="sonnet", errors=0):
    for i in range(n):
        conn.execute(
            "INSERT INTO agent_calls(agent, model, status, created_at) VALUES "
            "('orchestrator', ?, ?, ?)",
            (model, "error" if i < errors else status, (NOW - timedelta(minutes=5)).isoformat()),
        )
    conn.commit()


def _usage(project, pct):
    (project / "state" / "rate_limits.json").write_text(
        json.dumps(
            {
                "at": int((NOW - timedelta(minutes=1)).timestamp()),
                "rate_limits": {"five_hour": {"used_percentage": pct}},
            }
        ),
        encoding="utf-8",
    )


def test_llm_area_cap_usage_errors(conn, make_ctx, settings, project):
    settings.llm.daily_session_cap = 10
    assert areas.check_llm(make_ctx()) == []
    _sessions(conn, 8)
    _sessions(conn, 5, model="fake")  # fake 는 세지 않는다
    got = _keys(areas.check_llm(make_ctx()))
    assert got["llm.cap"].level == "watch" and "8/10" in got["llm.cap"].text
    assert got["llm.cap"].link == "/system#usage"
    _sessions(conn, 2)
    got = _keys(areas.check_llm(make_ctx()))
    assert got["llm.cap"].level == "warning" and "llm.errors" not in got

    _usage(project, 85)  # soft: 새 분석 거부·재평가 허용
    got = _keys(areas.check_llm(make_ctx()))
    assert got["llm.usage"].level == "watch" and "85%" in got["llm.usage"].text
    assert got["llm.usage"].link == "/system#usage"
    _usage(project, 97)  # hard: 전부 거부
    got = _keys(areas.check_llm(make_ctx()))
    assert got["llm.usage"].level == "warning"


def test_llm_errors_critical_and_watch(conn, make_ctx):
    _sessions(conn, 4, errors=3)
    got = _keys(areas.check_llm(make_ctx()))
    assert got["llm.errors"].level == "critical" and got["llm.errors"].count == 3
    assert got["llm.errors"].link == "/system#usage"
    conn.execute("DELETE FROM agent_calls")
    _sessions(conn, 4, errors=1)
    _sessions(conn, 3, errors=3, model="fake")  # fake 오류는 세지 않는다
    got = _keys(areas.check_llm(make_ctx()))
    assert got["llm.errors"].level == "watch" and got["llm.errors"].count == 1
    assert got["llm.errors"].link == "/system#usage"


def _order(conn, ticker, status, created_at, asset="stock", market="KR"):
    conn.execute(
        "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, status, mode, "
        "created_at, asset) VALUES ('paper-main', ?, ?, 'BUY', 1, 'LIMIT', ?, 'auto', ?, ?)",
        (ticker, market, status, created_at, asset),
    )


def _approval(conn, status, requested_at):
    conn.execute(
        "INSERT INTO approvals(account_id, order_json, status, requested_at, expires_at, "
        "fingerprint) VALUES ('paper-main', '{}', ?, ?, ?, 'fp')",
        (status, requested_at, requested_at),
    )


def test_orders_area_unknown_stale_approvals_rate_limiter(conn, make_ctx, monkeypatch):
    accts = [account("paper-main", ["KR"]), account("fo-1", ["KR"], "fo")]
    ports = {"accounts": FakeAccounts(accts)}
    assert areas.check_orders(make_ctx(ports=ports)) == []
    _order(conn, "005930", "unknown", NOW.isoformat())
    _order(conn, "000660", "unknown", NOW.isoformat(), asset="fo")  # fo 는 미집계
    _order(conn, "000660", "accepted", "2026-10-22T01:00:00+00:00")  # 전날 살아 있음
    _order(conn, "035420", "accepted", "2026-10-22T02:00:00+00:00", asset="fo")
    _order(conn, "035720", "filled", "2026-10-22T02:00:00+00:00")
    _approval(conn, "expired", (NOW - timedelta(hours=2)).isoformat())
    _approval(conn, "expired", (NOW - timedelta(hours=30)).isoformat())  # 24h 밖
    _approval(conn, "pending", NOW.isoformat())
    conn.commit()
    got = _keys(areas.check_orders(make_ctx(ports=ports)))
    assert got["orders.unknown"].level == "warning" and got["orders.unknown"].count == 1
    assert got["orders.unknown"].link == "/#orders-timeline"
    assert got["orders.stale"].level == "watch" and got["orders.stale"].count == 1
    assert got["orders.approvals_expired"].level == "watch"
    assert got["orders.approvals_expired"].count == 1
    assert got["orders.approvals_pending"].level == "watch"
    assert got["orders.approvals_pending"].count == 1
    assert got["orders.stale"].link == "/#orders-timeline"
    assert got["orders.approvals_expired"].link is None
    assert got["orders.approvals_pending"].link == "/#approvals"
    assert "orders.rate_limiter" not in got

    no_port = _keys(areas.check_orders(make_ctx()))
    assert "orders.stale" not in no_port and "orders.unknown" in no_port

    monkeypatch.setattr(
        rate_store,
        "disabled_status",
        lambda: [{"env": "real", "path": "/x/kis_rate.db", "since": "t", "error": "boom"}],
    )
    got = _keys(areas.check_orders(make_ctx()))
    hit = got["orders.rate_limiter"]
    assert hit.level == "warning" and "real" in hit.text
    assert hit.link == "/system#daemon"


def test_orders_stale_today_after_close(conn, make_ctx):
    """마감 뒤에는 오늘 낸 주문도 살아 있으면 stale, 마감 전에는 오늘 주문은 세지 않는다."""
    ports = {"accounts": FakeAccounts([account("paper-main", ["KR"])])}
    _order(conn, "005930", "accepted", (NOW - timedelta(hours=6)).isoformat())  # 오늘 11:30 KST
    conn.commit()
    got = _keys(areas.check_orders(make_ctx(ports=ports)))  # 17:30 KST, 마감 뒤
    assert got["orders.stale"].count == 1
    before = NOW - timedelta(hours=3)  # 14:30 KST, 마감 전
    assert "orders.stale" not in _keys(areas.check_orders(make_ctx(ports=ports, now=before)))
