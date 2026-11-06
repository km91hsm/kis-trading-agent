"""spot `approvals` 웹 자원: 목록·결정(선점)·실전 confirm·`approve_execute` 큐 본문.

웹은 주문을 내지 않는다 — 승인은 원장 선점 + `approve_execute` 큐 요청(202)까지이고, 집행은
데몬 큐 틱이 `executor.approve` 로 한다. 옛 동기 집행 테스트(브로커 거부·응답 없음·결정 변경)는
큐 본문 테스트로 옮겨 `job_requests.result` 로 단언한다."""

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from tests.features.conftest import AUTH
from tests.features.conftest import web_client as make_web_client
from tests.features.spot.approval_helpers import (
    Sent,
    fake_brokers,
    make_account,
    make_pending,
    make_port,
)
from trader.features.spot.feature import FEATURE
from trader.kernel.broker.errors import UNKNOWN_RESPONSE_CODE, KisApiError
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.queue_jobs import QueueState, queue_tick
from trader.kernel.daemon.runner import DaemonDeps
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import db
from trader.kernel.web.routers import jobs as jobs_router

FAST = frozenset({"fast"})


@pytest.fixture
def accounts():
    return [
        make_account("paper-main", "paper", "approve"),
        make_account("real-main", "real", "approve"),
    ]


@pytest.fixture
def brokers(accounts):
    return fake_brokers(accounts)


@pytest.fixture(autouse=True)
def _daemon_alive(monkeypatch):
    monkeypatch.setattr(jobs_router, "daemon_alive", lambda deps: True)


@pytest.fixture
def web_client(project, conn, accounts, brokers):
    return make_web_client(project, [FEATURE], ports={"accounts": make_port(accounts, brokers)})


@pytest.fixture
def pending_approval(conn, settings, accounts, brokers):
    return make_pending(conn, settings, accounts, brokers)


@pytest.fixture
def real_pending(conn, settings, accounts, brokers):
    return make_pending(conn, settings, accounts, brokers, account_id="real-main")


@pytest.fixture
def expired_approval(conn, settings, accounts, brokers):
    old = datetime.now(UTC) - timedelta(hours=1)
    return make_pending(conn, settings, accounts, brokers, now=lambda: old)


@pytest.fixture
def daemon_deps(project, settings, accounts, brokers):
    return DaemonDeps(
        settings=settings,
        schedule=None,
        conn_factory=db.connect,
        state=project / "state",
        now=lambda: datetime.now(UTC),
        markets=["KR"],
        gates={},
        stop_event=threading.Event(),
        dry_run=False,
        notifier=Sent(),
        ports={"accounts": make_port(accounts, brokers)},
        queue=QueueState(kinds=jobq.kinds_of([FEATURE])),
    )


def _requests(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM job_requests ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def _approval(conn, aid) -> dict:
    return xledger.get_approval(conn, aid)


def _patch(client, aid, body):
    return client.patch(f"/api/approvals/{aid}", json=body, headers=AUTH)


def _result(conn, req_id) -> dict:
    row = conn.execute("SELECT result FROM job_requests WHERE id=?", (req_id,)).fetchone()
    return json.loads(row["result"])


# --- 목록 --------------------------------------------------------------------------------


def test_get_approvals_lists_pending_with_summary_and_decision_ref(
    web_client, pending_approval, conn
):
    r = web_client.get("/api/approvals?status=pending")
    assert r.status_code == 200
    rows = r.json()["approvals"]
    # 두 계좌 모두 approve 라 결정 1건이 승인 요청 2건(모의·실전)을 만든다
    assert [x["id"] for x in rows] == [a["id"] for a in xledger.pending_approvals(conn)]
    row = next(x for x in rows if x["id"] == pending_approval)
    a = _approval(conn, pending_approval)
    assert row["decision"] == {"asset": "spot", "id": a["decision_id"]} and a["decision_id"]
    assert (row["account_id"], row["env"], row["ticker"], row["side"]) == (
        "paper-main", "paper", "005930", "BUY",
    )  # fmt: skip
    assert row["qty"] == a["order"]["qty"] and row["amount"]
    assert 0 < len(row["summary"]) <= 280 and "근거" in row["summary"]
    assert (row["status"], row["decided_by"], row["decided_channel"]) == ("pending", None, None)
    assert row["expires_at"] == a["expires_at"]


def test_get_approvals_rows_carry_ticker_name(web_client, pending_approval, conn):
    """승인 행도 종목명(원장 `ticker_names`, 없으면 None)을 싣는다 — 화면은 코드+이름 병기."""
    conn.execute(
        "INSERT INTO ticker_names(market, ticker, name, source, updated_at) "
        "VALUES ('KR', '005930', '삼성전자', 'test', '2026-11-04T00:00:00+00:00')"
    )
    conn.commit()
    rows = web_client.get("/api/approvals?status=pending").json()["approvals"]
    row = next(x for x in rows if x["id"] == pending_approval)
    assert row["name"] == "삼성전자"


def test_get_approvals_all_includes_decided(web_client, pending_approval):
    assert _patch(web_client, pending_approval, {"action": "reject"}).status_code == 200
    pending = web_client.get("/api/approvals").json()["approvals"]  # 기본은 pending
    assert pending_approval not in [x["id"] for x in pending]
    rows = web_client.get("/api/approvals?status=all").json()["approvals"]
    mine = next(x for x in rows if x["id"] == pending_approval)
    assert (mine["status"], mine["decided_channel"]) == ("rejected", "web")
    assert len(rows) == len(pending) + 1
    assert web_client.get("/api/approvals?status=bogus").status_code == 422


def test_summary_of_truncates_and_defaults(conn, pending_approval):
    from trader.features.spot import approvals

    a = _approval(conn, pending_approval)
    long = "\n".join(f"{i}번째 근거 줄 " + "가" * 120 for i in range(8))
    conn.execute(
        "UPDATE decisions SET data_json=json_set(data_json, '$.rationale', ?) WHERE id=?",
        (long, a["decision_id"]),
    )
    conn.commit()
    s = approvals.summary_of(conn, a)
    assert len(s) <= 280 and s.count("\n") <= 3 and s.startswith("0번째")
    assert approvals.summary_of(conn, {**a, "decision_id": 0}) == "근거 없음"


# --- 결정 --------------------------------------------------------------------------------


def test_patch_requires_token_and_same_origin(web_client, pending_approval, conn):
    url = f"/api/approvals/{pending_approval}"
    assert web_client.patch(url, json={"action": "approve"}).status_code == 401
    bad = {"Authorization": "Bearer t", "Origin": "http://evil.example"}
    assert web_client.patch(url, json={"action": "approve"}, headers=bad).status_code == 403
    assert _approval(conn, pending_approval)["decided_by"] is None and _requests(conn) == []


def test_patch_real_approval_requires_confirm(web_client, real_pending, conn):
    r = _patch(web_client, real_pending, {"action": "approve"})
    assert r.status_code == 400 and r.json()["detail"]["status"] == "confirm_required"
    r = _patch(web_client, real_pending, {"action": "approve", "confirm": "paper-main"})
    assert r.status_code == 400  # 다른 계좌 id 는 확인이 아니다
    a = _approval(conn, real_pending)
    assert a["decided_by"] is None and a["status"] == "pending"
    assert conn.execute("SELECT COUNT(*) FROM job_requests").fetchone()[0] == 0
    r = _patch(web_client, real_pending, {"action": "approve", "confirm": "real-main"})
    assert r.status_code == 202 and r.json()["status"] == "accepted"


def test_patch_paper_approve_enqueues_execute(web_client, pending_approval, conn, brokers):
    r = _patch(web_client, pending_approval, {"action": "approve"})
    assert r.status_code == 202
    body = r.json()
    reqs = _requests(conn)
    assert len(reqs) == 1 and body == {"status": "accepted", "request_id": reqs[0]["id"]}
    assert (reqs[0]["kind"], reqs[0]["source"], reqs[0]["status"]) == (
        "approve_execute", "web", "queued",
    )  # fmt: skip
    params = json.loads(reqs[0]["params"])
    assert params["approval_id"] == pending_approval and len(params["actor"]) == 8
    a = _approval(conn, pending_approval)
    assert (a["status"], a["decided_channel"], a["decided_by"]) == (
        "pending", "web", params["actor"],
    )  # fmt: skip
    assert brokers["paper-main"].orders == []  # 웹은 주문하지 않는다
    log = conn.execute("SELECT * FROM control_log").fetchall()
    assert [(x["key"], json.loads(x["new"]), x["channel"]) for x in log] == [
        (f"approval:{pending_approval}", "approve", "web")
    ]


def test_second_decide_conflicts_and_queue_has_one_request(web_client, pending_approval, conn):
    assert _patch(web_client, pending_approval, {"action": "approve"}).status_code == 202
    r = _patch(web_client, pending_approval, {"action": "approve"})
    assert r.status_code == 409 and r.json()["detail"]["ok"] is False
    assert _patch(web_client, pending_approval, {"action": "reject"}).status_code == 409
    assert conn.execute("SELECT COUNT(*) FROM job_requests").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM control_log").fetchone()[0] == 1


def test_patch_reject_marks_rejected(web_client, pending_approval, conn, brokers):
    r = _patch(web_client, pending_approval, {"action": "reject"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    a = _approval(conn, pending_approval)
    assert (a["status"], a["decided_channel"]) == ("rejected", "web")
    # 거부 알림은 데몬이 보낸다(웹은 알림을 쥐지 않는다) — 큐에는 approval_notice 만
    assert [r["kind"] for r in _requests(conn)] == ["approval_notice"]
    assert brokers["paper-main"].orders == []


def test_patch_expired_is_409(web_client, expired_approval, conn):
    r = _patch(web_client, expired_approval, {"action": "approve"})
    assert r.status_code == 409 and r.json()["detail"]["status"] == "expired"
    assert _approval(conn, expired_approval)["status"] == "expired" and _requests(conn) == []


def test_patch_missing_is_409_and_bad_action_422(web_client):
    r = _patch(web_client, 999, {"action": "approve"})
    assert r.status_code == 409 and r.json()["detail"]["ok"] is False
    assert _patch(web_client, 999, {"action": "maybe"}).status_code == 422


def test_patch_approve_when_daemon_down_is_409_without_decision(
    web_client, pending_approval, conn, monkeypatch
):
    monkeypatch.setattr(jobs_router, "daemon_alive", lambda deps: False)
    r = _patch(web_client, pending_approval, {"action": "approve"})
    assert r.status_code == 409 and "데몬" in r.json()["detail"]
    assert _approval(conn, pending_approval)["decided_by"] is None and _requests(conn) == []


def test_cancel_queued_approve_execute_releases_claim(web_client, pending_approval, conn):
    """웹에서 대기 중인 `approve_execute` 를 취소하면 그 승인의 선점이 풀린다(P14) — 다시 결정할
    수 있다."""
    r = _patch(web_client, pending_approval, {"action": "approve"})
    assert r.status_code == 202
    rid = r.json()["request_id"]
    assert _approval(conn, pending_approval)["decided_channel"] == "web"
    assert web_client.delete(f"/api/jobs/{rid}", headers=AUTH).status_code == 204
    a = _approval(conn, pending_approval)
    assert (a["status"], a["decided_by"], a["decided_channel"]) == ("pending", None, None)
    assert _patch(web_client, pending_approval, {"action": "approve"}).status_code == 202


def test_duplicate_enqueue_releases_claim(web_client, pending_approval, conn, monkeypatch):
    """`approve_execute` 를 못 넣으면(중복) 집행 없는 선점이 남지 않게 풀고 409 를 유지한다."""

    def dup(*a, **kw):
        raise jobq.DuplicateRequest("같은 요청이 이미 대기 중")

    monkeypatch.setattr(jobq, "enqueue", dup)
    r = _patch(web_client, pending_approval, {"action": "approve"})
    assert r.status_code == 409 and r.json()["detail"]["status"] == "duplicate"
    a = _approval(conn, pending_approval)
    assert (a["status"], a["decided_by"], a["decided_channel"]) == ("pending", None, None)


def test_reject_enqueues_approval_notice(web_client, pending_approval, conn, monkeypatch):
    """웹 거부 → 원장 결정 뒤 큐 `approval_notice`(P15). 큐 등록이 실패해도 거부 결과는 그대로."""
    r = _patch(web_client, pending_approval, {"action": "reject"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    (req,) = _requests(conn)
    assert (req["kind"], req["source"], req["status"]) == ("approval_notice", "web", "queued")
    params = json.loads(req["params"])
    assert (params["approval_id"], params["action"], params["channel"]) == (
        pending_approval, "reject", "web",
    )  # fmt: skip
    assert len(params["actor"]) == 8

    def boom(*a, **kw):
        raise RuntimeError("원장 잠김")

    monkeypatch.setattr(jobq, "enqueue", boom)
    # 같은 결정이 만든 실전 계좌 승인 요청 — 실전 거부는 confirm 불필요
    (real,) = [a["id"] for a in xledger.pending_approvals(conn) if a["account_id"] == "real-main"]
    r = _patch(web_client, real, {"action": "reject"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _approval(conn, real)["status"] == "rejected" and len(_requests(conn)) == 1


def test_approval_rows_have_market_and_currency(web_client, pending_approval, conn, accounts):
    """승인 행에 주문 시장·통화(시장 기준 KR→KRW, US→USD). 주문에 시장이 없으면 계좌의 유일한
    시장으로, 그것도 모르면 None."""
    from trader.features.spot import approvals

    rows = web_client.get("/api/approvals").json()["approvals"]
    row = next(x for x in rows if x["id"] == pending_approval)
    assert (row["market"], row["currency"]) == ("KR", "KRW")
    a = _approval(conn, pending_approval)
    us = {**a, "order": {**a["order"], "market": "US", "ticker": "AAPL"}}
    assert (approvals.row_of(conn, us, accounts)["market"],
            approvals.row_of(conn, us, accounts)["currency"]) == ("US", "USD")  # fmt: skip
    bare = {**a, "order": {k: v for k, v in a["order"].items() if k != "market"}}
    assert approvals.row_of(conn, bare, accounts)["currency"] == "KRW"  # paper-main 은 KR 만
    gone = {**bare, "account_id": "nobody"}
    assert (approvals.row_of(conn, gone, accounts)["market"],
            approvals.row_of(conn, gone, accounts)["currency"]) == (None, None)  # fmt: skip


# --- approve_execute 큐 본문 ----------------------------------------------------------------


def test_approve_execute_body_runs_core_and_is_idempotent(
    daemon_deps, pending_approval, web_client, conn, brokers
):
    assert _patch(web_client, pending_approval, {"action": "approve"}).status_code == 202
    queue_tick(daemon_deps, lanes=FAST)
    req = _requests(conn)[0]
    assert req["status"] == "ok"
    res = _result(conn, req["id"])
    assert res["ok"] is True and res["order"]["order_no"] == "FAKE-1"
    assert len(brokers["paper-main"].orders) == 1
    assert _approval(conn, pending_approval)["status"] == "approved"
    # 같은 params 를 다시 넣어 돌려도(재기동 재등록·수동 재요청) 주문은 다시 나가지 않는다
    rid = jobq.enqueue(
        conn, daemon_deps.queue.kinds, "approve_execute", json.loads(req["params"]),
        source="cli", now=datetime.now(UTC),
    )  # fmt: skip
    queue_tick(daemon_deps, lanes=FAST)
    again = _result(conn, rid)
    assert again["ok"] is False and again["status"] == "done"
    assert len(brokers["paper-main"].orders) == 1


def test_approve_execute_refuses_undecided_approval(daemon_deps, real_pending, conn, brokers):
    """`/api/jobs`·CLI 로 직접 넣은 요청은 decide(실전 confirm)를 우회하지 못한다."""
    rid = jobq.enqueue(
        conn, daemon_deps.queue.kinds, "approve_execute",
        {"approval_id": real_pending, "actor": None}, source="web", now=datetime.now(UTC),
    )  # fmt: skip
    queue_tick(daemon_deps, lanes=FAST)
    res = _result(conn, rid)
    assert res["ok"] is False and res["status"] == "not_decided"
    assert brokers["real-main"].orders == []
    assert _approval(conn, real_pending)["status"] == "pending"


def test_approve_execute_keeps_pending_releases_decision(
    daemon_deps, pending_approval, web_client, conn, brokers
):
    """일시정지 등으로 코어가 pending 을 유지하면 선점을 풀어 다시 결정할 수 있게 한다."""
    from trader.features.accounts import modes

    assert _patch(web_client, pending_approval, {"action": "approve"}).status_code == 202
    modes.set_paused(conn, True)
    queue_tick(daemon_deps, lanes=FAST)
    res = _result(conn, _requests(conn)[0]["id"])
    assert res["ok"] is False and res["status"] == "pending" and "일시정지" in res["error"]
    a = _approval(conn, pending_approval)
    assert (a["status"], a["decided_by"]) == ("pending", None)
    assert brokers["paper-main"].orders == []
    modes.set_paused(conn, False)
    assert _patch(web_client, pending_approval, {"action": "approve"}).status_code == 202


def test_approve_execute_broker_rejected_is_error_result(
    daemon_deps, pending_approval, web_client, conn, brokers
):
    """옛 `test_web_approve_broker_rejected_is_502` 이식: HTTP 502 대신 요청 결과가 오류."""
    old = brokers["paper-main"]
    brokers["paper-main"] = FakeBroker(old.account, cash=old.cash, fail={"place_order"})
    assert _patch(web_client, pending_approval, {"action": "approve"}).status_code == 202
    queue_tick(daemon_deps, lanes=FAST)
    req = _requests(conn)[0]
    res = _result(conn, req["id"])
    assert req["status"] == "error" and res["ok"] is False and res["status"] == "failed"
    assert "가짜 실패" in res["error"] and "브로커 거부" in req["error"]


def test_approve_execute_unknown_response_is_not_reported_as_rejection(
    daemon_deps, pending_approval, web_client, conn, brokers
):
    """옛 `test_web_approve_unknown_response_is_not_reported_as_rejection` 이식."""

    class Timeout(FakeBroker):
        def place_order(self, req):
            raise KisApiError(UNKNOWN_RESPONSE_CODE, "ReadTimeout", "TTTC0012U")

    old = brokers["paper-main"]
    brokers["paper-main"] = Timeout(old.account, cash=old.cash)
    assert _patch(web_client, pending_approval, {"action": "approve"}).status_code == 202
    queue_tick(daemon_deps, lanes=FAST)
    req = _requests(conn)[0]
    res = _result(conn, req["id"])
    assert res["unknown"] is True and "다시 누르지" in res["message"]
    assert res["order"]["status"] == "unknown" and "error" not in res
    assert req["status"] == "ok"


def test_approve_execute_skips_when_decision_changed_after_request(
    daemon_deps, pending_approval, web_client, conn, brokers
):
    """옛 `test_approve_skips_when_decision_changed_after_request` 이식."""
    assert _patch(web_client, pending_approval, {"action": "approve"}).status_code == 202
    conn.execute("UPDATE decisions SET action='HOLD'")
    conn.commit()
    queue_tick(daemon_deps, lanes=FAST)
    req = _requests(conn)[0]
    res = _result(conn, req["id"])
    assert req["status"] == "error" and "결정이 HOLD" in res["error"]
    assert brokers["paper-main"].orders == []
