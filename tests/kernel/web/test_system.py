"""커널 자원 `/api/system/*`·`/api/runs`·`/api/calls`: 헬스·상태·데몬·사용량·알림·제어 이력."""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
from datetime import timedelta

from tests.kernel.web.conftest import NOW, SAME, ledger_deps, make_client
from trader.kernel.ledger.control import log_change, set_flag
from trader.kernel.registry import Feature
from trader.kernel.registry.types import SessionKind
from trader.kernel.session import usage
from trader.kernel.web import health
from trader.kernel.web.routers import jobs as jobs_router
from trader.kernel.web.routers import system as sysr

LOCAL = ("127.0.0.1", 50000)


def _iso(dt) -> str:
    return dt.isoformat()


def _run(conn, kind, status, at):
    conn.execute(
        "INSERT INTO runs(kind, market, started_at, finished_at, status, error) "
        "VALUES (?, 'KR', ?, ?, ?, ?)",
        (kind, _iso(at), _iso(at), status, "boom" if status == "error" else None),
    )
    conn.commit()


def _call(conn, run_id, at, *, cost="0.5", model="sonnet"):
    conn.execute(
        "INSERT INTO agent_calls(run_id, agent, model, cost_usd, status, created_at) "
        "VALUES (?, 'orchestrator', ?, ?, 'ok', ?)",
        (run_id, model, cost, _iso(at)),
    )
    conn.commit()


def _alive_lock(state):
    (state / "daemon.lock").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")


def _cache(deps, *, at, level="warning", items=None, interval_s=None):
    from trader.kernel.ops import LABEL, store

    if items is None:
        items = [{"key": "orders.unknown", "level": "warning", "text": "응답 없는 주문 1건",
                  "link": "/", "count": 1, "extra": {}}]  # fmt: skip
    conn = deps.conn_factory()
    try:
        result = {"level": level, "label": LABEL[level], "at": at.isoformat(), "areas": {},
                  "items": items, "actions": []}  # fmt: skip
        if interval_s is not None:
            result["interval_s"] = interval_s
        store(conn, result)
    finally:
        conn.close()


def test_health_uses_daemon_cache_when_fresh(project):
    deps = ledger_deps(project)
    _alive_lock(deps.state)
    at = NOW - timedelta(minutes=5)
    _cache(deps, at=at)
    out = make_client(deps).get("/api/system/health").json()
    assert out["level"] == "warn" and out["checked_at"] == at.isoformat()
    assert out["reasons"] == [
        {"area": "orders", "level": "warn", "text": "응답 없는 주문 1건",
         "key": "orders.unknown", "link": "/", "count": 1}
    ]  # fmt: skip


def test_health_live_when_cache_missing_or_stale(project):
    deps = ledger_deps(project)
    out = make_client(deps).get("/api/system/health").json()  # 캐시 없음 → 웹이 직접 판정
    assert out["level"] == "fail" and out["checked_at"] == NOW.isoformat()
    assert any("데몬이 꺼져 있어요" in r["text"] for r in out["reasons"])
    _cache(deps, at=NOW - timedelta(hours=2), level="ok", items=[])  # 2 × 1800초 넘음
    out = make_client(deps).get("/api/system/health").json()
    assert out["level"] == "fail" and out["checked_at"] == NOW.isoformat()
    conn = deps.conn_factory()
    try:
        from trader.kernel.ops import latest

        assert latest(conn)["at"] == (NOW - timedelta(hours=2)).isoformat()  # 웹은 적지 않는다
    finally:
        conn.close()


def test_health_freshness_uses_recorded_interval(project):
    """데몬이 적은 실제 간격(60초)으로 신선도를 본다 — 2분 지난 결과는 stale(M-2)."""
    deps = ledger_deps(project)
    _alive_lock(deps.state)
    _cache(deps, at=NOW - timedelta(minutes=2), interval_s=60)
    out = make_client(deps).get("/api/system/health").json()
    assert out["checked_at"] == NOW.isoformat()  # 캐시를 버리고 직접 판정
    _cache(deps, at=NOW - timedelta(seconds=90), interval_s=60)
    out = make_client(deps).get("/api/system/health").json()
    assert out["checked_at"] == (NOW - timedelta(seconds=90)).isoformat()


def test_health_live_param_forces_check(project):
    deps = ledger_deps(project)
    _alive_lock(deps.state)
    _cache(deps, at=NOW - timedelta(minutes=5), level="warning")
    c = make_client(deps)
    cached = c.get("/api/system/health").json()
    assert cached["level"] == "warn" and cached["checked_at"] != NOW.isoformat()
    live = c.get("/api/system/health?live=1").json()
    assert live["checked_at"] == NOW.isoformat() and live["level"] == "ok"


def test_health_live_when_daemon_dead_despite_fresh_cache(project):
    """데몬이 ok 점검 직후 죽어도 캐시를 믿지 않는다 — 잠금이 없으면 직접 판정(T2-R3)."""
    deps = ledger_deps(project)
    _cache(deps, at=NOW - timedelta(minutes=1), level="ok", items=[])
    out = make_client(deps).get("/api/system/health").json()
    assert out["level"] == "fail" and out["checked_at"] == NOW.isoformat()
    assert any("데몬이 꺼져 있어요" in r["text"] for r in out["reasons"])


def test_health_live_reports_kernel_areas(project):
    """데몬이 살아 있고 오늘 세션이 상한에 닿으면 llm 영역 warn."""
    deps = ledger_deps(project)
    _alive_lock(deps.state)
    assert health.evaluate(deps, live=True)["level"] == "ok"
    deps.settings.llm.daily_session_cap = 10
    conn = deps.conn_factory()
    for i in range(10):
        _call(conn, None, NOW - timedelta(minutes=i + 1))
    conn.close()
    out = health.evaluate(deps, live=True)
    assert out["level"] == "warn"
    assert any(r["area"] == "llm" and "10/10" in r["text"] for r in out["reasons"])
    assert out["areas"]["llm"] == "warn" and out["areas"]["orders"] == "ok"


def test_health_ledger_failure_has_empty_areas(project):
    """원장을 못 읽어도 응답 모양(`areas`)은 같다."""

    def boom():
        raise sqlite3.OperationalError("locked")

    deps = dataclasses.replace(ledger_deps(project), conn_factory=boom)
    out = health.evaluate(deps, live=True)
    assert out["areas"] == {} and out["level"] == "fail"


def test_health_shape_unchanged(project):
    deps = ledger_deps(project)
    out = make_client(deps).get("/api/system/health").json()
    assert set(out) >= {"level", "reasons", "checked_at", "version", "features"}
    assert out["reasons"] and all(set(r) >= {"area", "level", "text"} for r in out["reasons"])
    assert out["level"] in {"ok", "warn", "fail"}


def test_status_and_daemon_endpoints(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    set_flag(conn, "some_flag", {"secret": "value"})
    conn.close()
    r = make_client(deps).get("/api/system/status").json()
    assert "데몬:" in r["text"]  # 커널 절은 늘 있다
    assert r["flags"] == ["some_flag"] and r["paused"] is None
    d = make_client(deps).get("/api/system/daemon").json()
    assert d["alive"] is False and d["pid"] is None
    ids = {j["id"]: j for j in d["jobs"]}
    assert ids["usage_probe"]["trigger"] == "interval" and ids["usage_probe"]["next_at"] is None


def test_notices_and_control_log_endpoints(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    for i, kind in enumerate(["order", "risk", "order"]):
        conn.execute(
            "INSERT INTO notices(at, kind, account_id, text, channel, ok) VALUES (?,?,?,?,?,?)",
            (_iso(NOW + timedelta(minutes=i)), kind, "paper-1", f"알림 {i}", "telegram", 1),
        )
    conn.commit()
    log_change(conn, channel="web", actor="me", key="paused", old=False, new=True, at=_iso(NOW))
    later = _iso(NOW + timedelta(minutes=1))
    log_change(conn, channel="telegram", actor="you", key="paused", old=True, new=False, at=later)
    conn.close()
    c = make_client(deps)
    rows = c.get("/api/system/notices").json()
    assert [r["text"] for r in rows] == ["알림 2", "알림 1", "알림 0"]
    assert rows[0]["ok"] is True and rows[0]["account_id"] == "paper-1"
    only = c.get("/api/system/notices", params={"kind": "order", "limit": 1}).json()
    assert [r["text"] for r in only] == ["알림 2"]
    log = c.get("/api/system/control-log").json()
    assert [(r["channel"], r["new"]) for r in log] == [("telegram", False), ("web", True)]
    assert len(c.get("/api/system/control-log", params={"limit": 1}).json()) == 1
    assert c.get("/api/system/notices", params={"limit": 0}).status_code == 422


def test_bootstrap_token_direct_local_only(project):
    deps = ledger_deps(project, extra_hosts=("mac.tailnet.ts.net",))
    local = make_client(deps, client=LOCAL)
    r = local.post("/api/system/bootstrap-token", headers=SAME)
    assert r.status_code == 200 and r.json() == {"token": "secret"}
    assert local.post("/api/system/bootstrap-token").status_code == 403  # 출처 없음
    remote = make_client(deps, client=("100.64.0.2", 1234))
    assert remote.post("/api/system/bootstrap-token", headers=SAME).status_code == 403
    proxied = local.post(
        "/api/system/bootstrap-token", headers={**SAME, "x-forwarded-for": "1.2.3.4"}
    )
    assert proxied.status_code == 403


def test_usage_endpoint_reports_windows_and_observed_limits(project, tmp_path):
    deps = ledger_deps(project)
    client = make_client(deps)
    r = client.get("/api/system/usage").json()
    assert r["rate_limits"] is None and r["windows"]["today"] == {"sessions": 0, "cap": 80}
    assert r["throttle"]["state"] == "ok"
    assert "usage_file" not in r and r["usage_file_present"] is False  # 홈 경로 노출 안 함
    p = tmp_path / "rl.json"
    p.write_text(
        json.dumps({"at": 0, "rate_limits": {"five_hour": {"used_percentage": 3}}}),
        encoding="utf-8",
    )
    deps.settings.llm.usage_file = str(p)
    r = client.get("/api/system/usage").json()
    assert r["rate_limits"]["five_hour"]["used_pct"] == 3.0 and "/usage" in r["note"]
    assert r["usage_file_present"] is True
    assert r["rate_limits"]["source"] == "statusline"
    conn = deps.conn_factory()
    usage.record_rate_limit(
        conn, {"unifiedWindows": {"five_hour": {"utilization": 0.85}}}, now=deps.now()
    )
    conn.close()
    r = client.get("/api/system/usage").json()
    assert (
        r["rate_limits"]["source"] == "session"
        and r["rate_limits"]["five_hour"]["used_pct"] == 85.0
    )
    assert r["throttle"]["state"] == "soft" and "보유 재평가만" in r["throttle"]["reason"]
    assert set(r["throttle"]) == {"enabled", "source", "soft_pct", "hard_pct", "state", "reason"}


def test_usage_ensure_fresh_enqueues_probe_only_when_stale(project, monkeypatch):
    """페이지 접속·새로고침(토큰 없음): 마지막 사용량 관측이 5분보다 오래됐고 데몬이 살아 있으면
    대기열에 usage_probe 를 넣는다 — 60초에 한 번, 이미 대기 중이면 그대로, 다른 사이트는 403."""
    deps = ledger_deps(project)
    client = make_client(deps)
    sysr._USAGE_ENSURE_LAST[0] = 0.0
    monkeypatch.setattr(jobs_router, "daemon_alive", lambda deps: False)
    assert client.post("/api/system/usage/ensure-fresh", headers=SAME).json() == {
        "skipped": "데몬 꺼짐"
    }
    monkeypatch.setattr(jobs_router, "daemon_alive", lambda deps: True)
    r = client.post("/api/system/usage/ensure-fresh", headers=SAME).json()
    assert r["queued"] is True and r["id"]
    conn = deps.conn_factory()
    kinds = [x[0] for x in conn.execute("SELECT kind FROM job_requests").fetchall()]
    assert kinds == ["usage_probe"]
    assert client.post("/api/system/usage/ensure-fresh", headers=SAME).json() == {"throttled": True}
    sysr._USAGE_ENSURE_LAST[0] = 0.0
    assert client.post("/api/system/usage/ensure-fresh", headers=SAME).json()["already"] is True
    conn.execute(
        "INSERT INTO llm_rate_limits(observed_at, source, five_hour_util, raw_json) "
        "VALUES (?, 'probe', 0.2, '{}')",
        ((NOW - timedelta(minutes=1)).isoformat(),),
    )
    conn.commit()
    conn.close()
    assert client.post("/api/system/usage/ensure-fresh", headers=SAME).json()["fresh"] is True
    bad = client.post("/api/system/usage/ensure-fresh", headers={"Origin": "http://evil.example"})
    assert bad.status_code == 403


def _sk(kind, runs_kind, cap_key=None):
    return SessionKind(
        kind=kind, agents=(), prompt_section="x", schemas=(), timeout_key="x",
        runs_kind=runs_kind, extra_cap_key=cap_key, throttle="hard", fake=lambda *a: None,
    )  # fmt: skip


def test_usage_caps_by_kind(project):
    feat = Feature(
        id="fx",
        tier="service",
        session_kinds=(_sk("fx_market", "fx_market", "market_session_cap"), _sk("fx_a", "fx_a")),
    )
    deps = ledger_deps(project, features=[feat])
    conn = deps.conn_factory()
    _run(conn, "fx_market", "ok", NOW - timedelta(minutes=10))
    _call(conn, 1, NOW - timedelta(minutes=9))
    _call(conn, 1, NOW - timedelta(minutes=8), model="fake")  # fake 는 세지 않는다
    conn.close()
    r = make_client(deps).get("/api/system/usage").json()
    assert r["caps_by_kind"] == [
        {"kind": "fx_market", "used": 1, "cap": 2},
        {"kind": "fx_a", "used": 0, "cap": None},
    ]


def test_runs_calls_csv(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    _run(conn, "fx_scan", "ok", NOW - timedelta(minutes=30))
    _run(conn, "fx_other", "error", NOW - timedelta(days=2))
    _call(conn, 1, NOW - timedelta(minutes=29), cost="1.5")
    _call(conn, 2, NOW - timedelta(days=2), cost="0.25")
    conn.close()
    c = make_client(deps)
    runs = c.get("/api/runs").json()
    assert [r["id"] for r in runs] == [2, 1]
    assert [r["kind"] for r in c.get("/api/runs", params={"kind": "fx_scan"}).json()] == ["fx_scan"]
    day = (NOW - timedelta(days=2)).date().isoformat()
    assert [r["id"] for r in c.get("/api/runs", params={"date": day}).json()] == [2]
    assert c.get("/api/runs", params={"date": "nope"}).status_code == 400
    assert c.get("/api/runs", params={"limit": 0}).status_code == 422
    calls = c.get("/api/calls").json()
    assert calls["sessions_today"] == 1 and calls["cost_today"] == 1.5
    assert [x["run_id"] for x in calls["calls"]] == [2, 1]
    assert [x["run_id"] for x in c.get("/api/calls", params={"run_id": 1}).json()["calls"]] == [1]
    r = c.get("/api/runs", params={"format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.startswith("﻿id,kind,market,started_at,finished_at,status,error")
    assert "attachment" in r.headers["content-disposition"]
    r2 = c.get("/api/calls", params={"format": "csv", "run_id": 1})
    lines = r2.text.lstrip("﻿").splitlines()
    assert r2.headers["content-type"].startswith("text/csv") and len(lines) == 2
    assert lines[0].startswith("id,run_id,")


def test_csv_response_columns_and_empty():
    from trader.kernel.web.csv import csv_response

    r = csv_response([{"a": 1, "b": None, "c": "x"}], "t.csv", columns=["c", "a", "b"])
    assert r.body.decode("utf-8") == "﻿c,a,b\r\nx,1,\r\n"
    assert csv_response([], "e.csv", columns=None).body.decode("utf-8") == "﻿\r\n"


def test_csv_response_nested_values_are_json():
    """목록·사전 칸은 repr 이 아니라 JSON(한글 그대로)."""
    from trader.kernel.web.csv import csv_response

    r = csv_response([{"a": [{"q": 1}], "b": {"k": "값"}}], "t.csv")
    assert r.body.decode("utf-8").lstrip("﻿").splitlines()[1] == '"[{""q"": 1}]","{""k"": ""값""}"'


def test_usage_throttle_toggle_api(project):
    """설정 화면: 사용량 감속 켜기·끄기(토큰 + 같은 출처), control 이력에 web 으로 남고
    /api/system/usage 가 실효 값과 출처를 보인다. null 이면 화면 설정을 지우고 yaml 값으로."""
    from tests.kernel.web.conftest import AUTH

    deps = ledger_deps(project)
    c = make_client(deps)
    url = "/api/settings/usage-throttle"
    assert c.patch(url, json={"enabled": False}).status_code == 401
    assert c.patch(url, json={"enabled": False}, headers={"Authorization": "Bearer secret"}
                   ).status_code == 403  # fmt: skip
    assert c.patch(url, json={"enabled": "x"}, headers=AUTH).status_code == 422
    assert c.patch(url, json={"enabled": False, "x": 1}, headers=AUTH).status_code == 422
    r = c.patch(url, json={"enabled": False}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"enabled": False, "source": "control"}
    t = c.get("/api/system/usage").json()["throttle"]
    assert t["enabled"] is False and t["source"] == "control"
    log = c.get("/api/system/control-log").json()
    assert [(x["channel"], x["key"], x["old"], x["new"]) for x in log] == [
        ("web", usage.THROTTLE_FLAG, None, False)
    ]
    assert log[0]["actor"] and log[0]["actor"] != "secret"  # 토큰 지문, 토큰 자체가 아님
    r = c.patch(url, json={"enabled": None}, headers=AUTH)
    assert r.json() == {"enabled": True, "source": "yaml"}
    assert len(c.get("/api/system/control-log").json()) == 2


def test_session_cap_api(project):
    """설정 화면: Claude 하루 세션 제한 변경(토큰 + 같은 출처, 1~1000), control 이력, null 이면
    yaml 값으로. /api/system/usage 가 실효 값·출처·기본값과 오늘 창의 cap 을 보인다."""
    from tests.kernel.web.conftest import AUTH

    deps = ledger_deps(project)
    c = make_client(deps)
    url = "/api/settings/session-cap"
    default = deps.settings.llm.daily_session_cap
    assert c.patch(url, json={"cap": 5}).status_code == 401
    for bad in (0, 1001, "x", 2.5, True, "5"):
        assert c.patch(url, json={"cap": bad}, headers=AUTH).status_code == 422, bad
    assert c.patch(url, json={"cap": 5, "x": 1}, headers=AUTH).status_code == 422
    r = c.patch(url, json={"cap": 5}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"value": 5, "source": "control", "default": default}
    u = c.get("/api/system/usage").json()
    assert u["session_cap"] == {"value": 5, "source": "control", "default": default}
    assert u["windows"]["today"]["cap"] == 5
    log = c.get("/api/system/control-log").json()
    assert [(x["key"], x["old"], x["new"]) for x in log] == [(usage.SESSION_CAP_FLAG, None, 5)]
    r = c.patch(url, json={"cap": None}, headers=AUTH)
    assert r.json() == {"value": default, "source": "yaml", "default": default}


def test_notices_date_filter_is_kst_day(project):
    """`date=` 는 KST 하루 [00:00, 24:00) — UTC 로는 전날 15:00 부터. kind 와 같이 쓸 수 있고
    형식이 틀리면 422."""
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    rows = [
        ("2026-11-01T14:30:00+00:00", "order", "a"),  # KST 11-01 23:30
        ("2026-11-01T15:30:00+00:00", "risk", "b"),  # KST 11-02 00:30
        ("2026-11-02T14:00:00+00:00", "order", "c"),  # KST 11-02 23:00
        ("2026-11-02T15:00:00+00:00", "order", "d"),  # KST 11-03 00:00
    ]
    for at, kind, text in rows:
        conn.execute(
            "INSERT INTO notices(at, kind, account_id, text, channel, ok) VALUES (?,?,?,?,?,?)",
            (at, kind, None, text, "telegram", 1),
        )
    conn.commit()
    conn.close()
    c = make_client(deps)
    day = c.get("/api/system/notices", params={"date": "2026-11-02"}).json()
    assert [r["text"] for r in day] == ["c", "b"]
    both = c.get("/api/system/notices", params={"date": "2026-11-02", "kind": "order"}).json()
    assert [r["text"] for r in both] == ["c"]
    assert c.get("/api/system/notices", params={"date": "2026-13-40"}).status_code == 422
    assert len(c.get("/api/system/notices").json()) == 4


def test_pause_view_reports_latest_log(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    set_flag(conn, "paused", True)
    at = _iso(NOW)
    log_change(conn, channel="tray", actor=None, key="paused", old=False, new=True, at=at)
    conn.close()
    r = make_client(deps).get("/api/system/pause").json()
    assert r == {"paused": True, "at": at, "by": "tray"}


def test_pause_view_without_log(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    set_flag(conn, "paused", True)
    conn.close()
    r = make_client(deps).get("/api/system/pause").json()
    assert r == {"paused": True, "at": None, "by": None}


def test_pause_view_stale_log_ignored(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    set_flag(conn, "paused", True)
    log_change(conn, channel="web", actor=None, key="paused", old=True, new=False, at=_iso(NOW))
    conn.close()
    r = make_client(deps).get("/api/system/pause").json()
    assert r == {"paused": True, "at": None, "by": None}


def test_status_includes_paused_at(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    set_flag(conn, "paused", True)
    at = _iso(NOW)
    log_change(conn, channel="telegram", actor="x", key="paused", old=False, new=True, at=at)
    conn.close()
    r = make_client(deps).get("/api/system/status").json()
    assert r["paused_at"] == at and r["paused_by"] == "telegram"
