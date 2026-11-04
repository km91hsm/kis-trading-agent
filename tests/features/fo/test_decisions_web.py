"""fo decisions·orders 조각과 `GET /decisions/fo/{id}`·`/trace`.

신규 `test_decision_detail_paths_by_asset`(`/decisions/fo/1` 과 `/decisions/spot/1` 은 다른 행) +
fo 결정 행 상태·계좌 필터, 주문 다리 묶음(`legs`), trace 체크포인트."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.features.accounts.conftest import make_account
from tests.features.conftest import web_client as make_web_client
from tests.features.fo.conftest import ACCT, NOW, TODAY
from tests.features.spot.helpers import ok_decision
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.fo import store
from trader.features.fo.feature import FEATURE as FO
from trader.features.spot.feature import FEATURE as SPOT
from trader.features.spot.ledger import ingest_checkpoint
from trader.kernel.config.secrets import Secrets
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import db
from trader.kernel.session import run_store

D = TODAY.isoformat()


@pytest.fixture
def client(project):
    accounts = [
        make_account("paper-main", "paper", "auto"),
        make_account(ACCT, "paper", "auto", ["KR"], acnt_prdt_cd="03", asset="fo"),
        make_account("paper-fo2", "paper", "auto", ["KR"], acnt_prdt_cd="03", asset="fo"),
    ]
    ports = {"accounts": AccountsPortImpl(accounts, Secrets())}
    return make_web_client(project, [SPOT, FO], ports=ports, now=NOW)


@pytest.fixture
def ledger(project, client):
    c = db.connect(project / "state" / "trader.db")
    try:
        yield c
    finally:
        c.close()


def _fo_decision(conn, *, account_id=None, position_id=None, rnd=0, request_id="rq0",
                 intent="ENTER", at="2026-10-27T00:10:00+00:00", data='{"decision": {}}'):
    cur = conn.execute(
        "INSERT INTO fo_decisions(date, account_id, position_id, round, request_id, intent, "
        "direction, vehicle, confidence, data, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'LONG', 'FUTURES', 0.6, ?, ?)",
        (D, account_id, position_id, rnd, request_id, intent, data, at),
    )
    conn.commit()
    return int(cur.lastrowid)


def _position(conn):
    with store.tx(conn):
        return store.insert_position(
            conn, account_id=ACCT, code="105W11", kind="FUTURES", contract_side="LONG",
            direction="LONG", vehicle="FUTURES", status="held", qty_target=2, qty_held=2,
            stop_pt=Decimal("345"), decision_date=D, opened_at="2026-10-27T00:30:00+00:00",
        )  # fmt: skip


def _leg(conn, pid, *, attempt=1, status="filled", request_id="rq0", at, order_id=None,
         filled=0, rnd=0, intent="ENTRY"):
    conn.execute(
        "INSERT INTO fo_order_legs(position_id, account_id, intent, round, attempt, status, "
        "order_id, order_no, qty, filled_qty, price, order_type, created_at, request_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, ?, '350', 'LIMIT', ?, ?)",
        (pid, ACCT, intent, rnd, attempt, status, order_id, f"N{attempt}", filled, at, request_id),
    )
    conn.commit()


def test_decision_detail_paths_by_asset(client, ledger):
    """같은 id 1 이라도 자산별 경로가 다른 표를 읽는다."""
    ingest_checkpoint(ledger, TODAY, "KR", "005930", "30_decision", {"data": ok_decision()})
    fid = _fo_decision(ledger, data='{"decision": {"stop_pt": 345, "rationale": "추세"}}')
    assert fid == 1 and xledger.decision_id(ledger, TODAY, "KR", "005930") == 1
    spot = client.get("/api/decisions/spot/1").json()
    fo = client.get("/api/decisions/fo/1").json()
    assert spot["decision"]["ticker"] == "005930" and "intent" not in spot["decision"]
    assert fo["decision"]["intent"] == "ENTER" and fo["decision"]["stop_pt"] == 345
    assert fo["decision"]["rationale"] == "추세" and fo["legs"] == []
    assert client.get("/api/decisions/fo/99").status_code == 404
    assert client.get("/api/decisions/fo/99/trace").status_code == 404


def test_fo_decision_rows_status_and_account_filter(client, ledger):
    pid = _position(ledger)
    morning = _fo_decision(ledger)
    reeval = _fo_decision(ledger, account_id="paper-fo2", position_id=pid, rnd=1,
                          request_id="rq1", intent="CLOSE", at="2026-10-27T02:00:00+00:00",
                          data='{"record_only": true}')  # fmt: skip
    _leg(ledger, pid, status="cancelled", at="2026-10-27T00:31:00+00:00")
    _leg(ledger, pid, attempt=2, status="filled", at="2026-10-27T00:35:00+00:00")
    items = client.get("/api/decisions").json()["items"]
    rows = {x["id"]: x for x in items if x["asset"] == "fo"}
    assert rows[morning]["status"] == "filled"  # 그 결정 봉투의 마지막 다리
    assert rows[morning]["account_id"] is None and rows[morning]["intent"] == "ENTER"
    assert rows[morning]["at"] == "2026-10-27T00:10:00+00:00"
    assert rows[reeval]["status"] == "record_only"
    mine = client.get("/api/decisions", params={"account_id": ACCT}).json()["items"]
    assert [x["id"] for x in mine if x["asset"] == "fo"] == [morning]  # 아침 결정은 모든 계좌
    assert client.get("/api/decisions", params={"date": "2026-10-26"}).json()["items"] == []


def test_fo_orders_group_attempts_into_legs(client, ledger):
    """같은 (포지션·의도·라운드) 재시도는 한 행, 시도들은 `legs`. 상태는 마지막 시도."""
    pid = _position(ledger)
    oid = xledger.insert_order(
        ledger, decision_id=None, account_id=ACCT, ticker="105W11", market="KR", side="BUY",
        qty=2, order_type="LIMIT", price=Decimal("350"), status="filled", order_no="N2",
        message="", mode="auto", created_at="2026-10-27T00:35:00+00:00", asset="fo",
        origin="fo",
    )  # fmt: skip
    _leg(ledger, pid, status="cancelled", at="2026-10-27T00:31:00+00:00")
    _leg(ledger, pid, attempt=2, status="filled", order_id=oid, filled=2,
         at="2026-10-27T00:35:00+00:00")  # fmt: skip
    _leg(ledger, pid, intent="EXIT", status="accepted", at="2026-10-26T05:00:00+00:00")  # 어제
    body = client.get("/api/orders", params={"account_id": ACCT}).json()
    assert body["partial"] is False
    (row,) = body["items"]  # 현물 조각의 asset='fo' 주문 행은 중복으로 나오지 않는다
    assert row["asset"] == "fo" and row["lane"] == "fo" and row["origin"] == "fo"
    assert row["ticker"] == "105W11" and row["side"] == "BUY" and row["status"] == "filled"
    assert row["filled_qty"] == 2 and row["order_no"] == "N2" and row["intent"] == "ENTRY"
    assert row["at"] == "2026-10-27T00:31:00+00:00"
    assert [leg["attempt"] for leg in row["legs"]] == [1, 2]
    assert client.get("/api/orders", params={"account_id": "paper-main"}).json()["items"] == []
    st = client.get("/api/orders", params={"account_id": ACCT, "status": "accepted"}).json()
    assert st["items"] == []


def test_fo_decision_trace_reads_fo_checkpoints(client, ledger, project):
    fid = _fo_decision(ledger)
    mdir = run_store.market_dir(TODAY, "KR", project / "state", create=True)
    run_store.write_checkpoint(mdir, "_fo_analyst_technical",
                               {"data": {"signal": "BUY", "confidence": 0.6, "summary": "상승"}})
    run_store.write_checkpoint(mdir, "_fo_decision", {"data": {"intent": "ENTER"}})
    run_store.write_checkpoint(mdir, f"_fo_risk_check_{ACCT}",
                               {"data": {"verdict": {"approved": False, "reasons": ["한도"]}}})
    run_store.write_checkpoint(mdir, "_scan", {"picks": []})  # 현물 단계는 빠진다
    tr = client.get(f"/api/decisions/fo/{fid}/trace").json()
    assert tr["decision"]["id"] == fid
    assert tr["stages"] == ["_fo_analyst_technical", "_fo_decision", f"_fo_risk_check_{ACCT}"]
    assert tr["analysts"] == [
        {"role": "technical", "signal": "BUY", "confidence": 0.6, "summary": "상승"}
    ]
    assert tr["checkpoint"] == {"intent": "ENTER"}
    assert tr["checks"][f"_fo_risk_check_{ACCT}"]["approved"] is False


def test_fo_parts_skip_non_fo_account_filter(client, ledger):
    """계좌 필터가 현물·모르는 계좌면 파생 조각은 비어 있다(아침 결정 account_id NULL 포함)."""
    pid = _position(ledger)
    _fo_decision(ledger)
    _leg(ledger, pid, at="2026-10-27T00:31:00+00:00")
    for acct in ("paper-main", "ghost"):
        assert client.get("/api/decisions", params={"account_id": acct}).json()["items"] == []
        assert client.get("/api/orders", params={"account_id": acct}).json()["items"] == []
    assert len(client.get("/api/decisions", params={"account_id": ACCT}).json()["items"]) == 1
