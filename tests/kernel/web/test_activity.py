"""합성 원장으로 작업의 범위·시간·중단 분류와 읽기 전용 조회를 검증한다."""

import json
from datetime import timedelta

import pytest

from tests.kernel.web.conftest import NOW, ledger_deps, make_client


def _run(conn, *, kind="analyze", market="US", status="error", start=-60, end=-50,
         error="타임아웃", params=None):
    result = None if params is None else json.dumps({"params": params})
    return conn.execute(
        "INSERT INTO runs(kind,market,started_at,finished_at,status,error,result_json) "
        "VALUES (?,?,?,?,?,?,?)",
        (kind, market, (NOW + timedelta(minutes=start)).isoformat(),
         None if end is None else (NOW + timedelta(minutes=end)).isoformat(),
         status, error, result),
    ).lastrowid


def _request(conn, *, params, status="error", start=-60, end=-50):
    return conn.execute(
        "INSERT INTO job_requests(kind,params,status,requested_at,started_at,finished_at,error) "
        "VALUES ('analyze',?,?,?,?,?,?)",
        (json.dumps(params), status, (NOW + timedelta(minutes=start - 1)).isoformat(),
         (NOW + timedelta(minutes=start)).isoformat(),
         (NOW + timedelta(minutes=end)).isoformat(), "타임아웃" if status == "error" else None),
    ).lastrowid


def test_runs_stop_request_is_interrupted_without_rewriting_error(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    _run(conn, error="세션 중단 (종료 요청)")
    conn.commit()
    before = list(map(tuple, conn.execute("SELECT * FROM runs")))
    row = make_client(deps).get("/api/runs").json()[0]
    assert row["outcome"] == "interrupted"
    assert row["status"] == "error" and row["error"] == "세션 중단 (종료 요청)"
    assert row["followup_at"] is None
    assert list(map(tuple, conn.execute("SELECT * FROM runs"))) == before
    conn.close()


@pytest.mark.parametrize("changed", [{"ticker": "BBB"}, {"account_id": "paper-b"},
                                      {"market": "KR"}, {"years": 2}])
def test_requests_different_scope_does_not_clear_failure(project, changed):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    params = {"market": "US", "ticker": "AAA", "account_id": "paper-a", "years": 1}
    failed = _request(conn, params=params)
    _request(conn, params={**params, **changed}, status="ok", start=-40, end=-30)
    conn.commit()
    rows = make_client(deps).get("/api/jobs/requests").json()["requests"]
    row = next(r for r in rows if r["id"] == failed)
    assert row["outcome"] == "failed" and row["followup_at"] is None
    conn.close()


@pytest.mark.parametrize("status,start,end,want", [
    ("ok", -40, -30, "followed_by_success"),
    ("ok", -55, -30, "failed"),  # 실패가 끝나기 전에 시작한 병행 실행
    ("skipped", -40, -30, "failed"),
    ("error", -40, -30, "failed"),
])
def test_requests_only_later_started_success_is_followup(project, status, start, end, want):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    params = {"ticker": "AAA", "market": "US", "account_id": "paper-a"}
    failed = _request(conn, params=params)
    _request(conn, params=dict(reversed(list(params.items()))), status=status, start=start, end=end)
    conn.commit()
    rows = make_client(deps).get("/api/jobs/requests").json()["requests"]
    row = next(r for r in rows if r["id"] == failed)
    assert row["outcome"] == want
    assert row["followup_at"] == ((NOW - timedelta(minutes=30)).isoformat()
                                     if want == "followed_by_success" else None)
    conn.close()


def test_runs_unknown_scope_stays_unconfirmed_even_after_same_market_success(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    failed = _run(conn)
    _run(conn, status="ok", start=-40, end=-30, error=None)
    conn.commit()
    row = next(r for r in make_client(deps).get("/api/runs").json() if r["id"] == failed)
    assert row["outcome"] == "failed" and row["followup_at"] is None
    conn.close()


def test_activity_separates_active_steps_requests_and_24h_history(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    scope = {"market": "US", "ticker": "AAA", "account_id": "paper-a"}
    _run(conn, params=scope)
    _run(conn, status="ok", start=-40, end=-30, error=None, params=scope)
    _run(conn, kind="collect", error="데몬 종료 요청으로 중단")
    _run(conn, kind="scan", start=-1440, end=-1430)  # 24시간 경계 포함
    _run(conn, kind="old", start=-1441, end=-1431)  # 경계 밖
    _run(conn, kind="scan_analyze", status="running", start=-20, end=None, error=None)
    _run(conn, kind="analyze", status="running", start=-10, end=None, error=None)
    conn.commit()
    before = conn.total_changes
    out = make_client(deps).get("/api/jobs/activity").json()
    assert out["queued_requests"] == out["running_requests"] == 0
    assert {r["kind"] for r in out["running_runs"]} == {"scan_analyze", "analyze"}
    assert out["recent_errors"] == {"total": 3, "failed": 1, "interrupted": 1,
                                     "followed_by_success": 1}
    assert conn.total_changes == before
    conn.close()


def test_jobs_health_describes_history_and_preserves_unconfirmed_failures(project):
    from trader.kernel.ops.areas import check_jobs
    from trader.kernel.ops.types import OpsContext

    deps = ledger_deps(project)
    conn = deps.conn_factory()
    for _ in range(5):
        _run(conn, error="세션 중단 (종료 요청)")
    _run(conn, kind="unconfirmed")
    conn.commit()
    ctx = OpsContext(conn=conn, settings=deps.settings, state=deps.state, now=NOW,
                     ports={}, jobs=[], features=[])
    history = next(i for i in check_jobs(ctx) if i.key == "jobs.errors")
    assert history.level == "watch" and history.count == 6
    assert "확인 필요 1건" in history.text and "종료 요청 중단 5건" in history.text
    conn.close()


@pytest.mark.parametrize("params", [None, {}, {"market": "US", "ticker": "AAA"},
                                    {"market": "KR", "ticker": "AAA", "account_id": "paper-a"}])
def test_runs_partial_or_conflicting_scope_is_explicitly_unconfirmed(project, params):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    _run(conn, params=params)
    _run(conn, params=params, status="ok", start=-40, end=-30, error=None)
    conn.commit()
    rows = make_client(deps).get("/api/runs").json()
    assert rows[-1]["scope_confirmed"] is False
    assert rows[-1]["outcome"] == "failed"
    conn.close()


def test_filtered_runs_find_success_outside_selected_day_and_keep_csv_errors(project):
    import csv
    import io

    deps = ledger_deps(project)
    conn = deps.conn_factory()
    params = {"market": "US", "ticker": "AAA", "account_id": "paper-a", "years": 1}
    _run(conn, params=params, start=-1500, end=-1490)
    _run(conn, params=params, status="ok", start=-60, end=-50, error=None)
    conn.commit()
    client = make_client(deps)
    previous_day = (NOW - timedelta(days=1)).date().isoformat()
    response = client.get("/api/runs", params={"date": previous_day})
    assert len(response.json()) == 1
    assert response.json()[0]["outcome"] == "followed_by_success"
    exported = client.get("/api/runs", params={"date": previous_day, "format": "csv"})
    row = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))[0]
    assert row["status"] == "error" and row["error"] == "타임아웃"
    assert row["outcome"] == "followed_by_success" and row["followup_at"]
    conn.close()


def test_shutdown_mention_in_an_unrelated_error_is_not_interrupted(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    _run(conn, error="종료 요청 설정을 읽지 못함: DB 오류")
    conn.commit()
    row = make_client(deps).get("/api/runs").json()[0]
    assert row["outcome"] == "failed"
    conn.close()


def test_skipped_shutdown_request_is_interrupted_but_not_success(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    req_id = _request(conn, params={"market": "US"}, status="skipped")
    conn.execute("UPDATE job_requests SET error='데몬 종료 중' WHERE id=?", (req_id,))
    conn.commit()
    row = make_client(deps).get("/api/jobs/requests").json()["requests"][0]
    assert row["status"] == "skipped" and row["outcome"] == "interrupted"
    assert row["followup_at"] is None
    conn.close()


def test_interrupted_history_remains_visible_without_raising_health_level(project):
    deps = ledger_deps(project)
    conn = deps.conn_factory()
    _run(conn, error="세션 중단 (종료 요청)")
    conn.commit()
    conn.close()
    out = make_client(deps).get("/api/system/health").json()
    reason = next(r for r in out["reasons"] if r.get("key") == "jobs.errors")
    assert reason["level"] == "info" and "종료 요청 중단 1건" in reason["text"]


def test_daemon_last_outcome_uses_same_shutdown_classification_without_changing_status(project):
    deps = ledger_deps(project)
    (deps.state / "daemon_heartbeat.json").write_text(json.dumps({
        "updated_at": NOW.isoformat(),
        "jobs": {"usage_probe": {"last_status": "error", "last_error": "데몬 종료 요청으로 중단"}},
    }), encoding="utf-8")
    jobs = make_client(deps).get("/api/system/daemon").json()["jobs"]
    row = next(r for r in jobs if r["id"] == "usage_probe")
    assert row["last_status"] == "error" and row["last_outcome"] == "interrupted"
    assert row["last_error"] == "데몬 종료 요청으로 중단"
