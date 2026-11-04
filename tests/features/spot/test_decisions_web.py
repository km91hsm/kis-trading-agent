"""spot decisions·orders 자원: merge 조각·상세·trace·outcomes·체크포인트·news-refs·시장 today·
risk-checks·CSV·SSE `decisions`·`orders`·`triggers`.

옛 `tests/web/test_runs_charts.py` 의 `test_today_*` 3건, `test_app.py` 의
`test_decision_trace_endpoint`·`test_csv_format_for_orders_decisions_positions`,
`test_accounts_portfolio_orders.py` 의 orders 2건(`test_account_id_required` 는 merge 로 계좌가
선택 필터가 되어 '계좌별 분리'·'필터' 로 다시 씀), `test_control_sse.py` 의
`test_sse_decisions_fingerprint_sees_same_day_update`·`test_sse_snapshot_detects_status_changes_
without_new_rows` 이식 + 신규 `test_decisions_merged_spot_and_fo_by_date`·
`test_news_refs_from_analyst_evidence`."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.features.accounts.conftest import make_account
from tests.features.conftest import web_client as make_web_client
from tests.features.spot.helpers import ok_decision, ok_report, ok_turn
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.fo.feature import FEATURE as FO
from trader.features.spot import routes as spot_routes
from trader.features.spot.feature import FEATURE as SPOT
from trader.features.spot.ledger import ingest_checkpoint
from trader.kernel.config.secrets import Secrets
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import db
from trader.kernel.registry.types import NavItem
from trader.kernel.session import run_store

NOW = datetime(2026, 11, 4, 6, 0, tzinfo=UTC)  # KST 15:00
D = date(2026, 11, 4)


@pytest.fixture
def accounts():
    return [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("real-main", "real", "off"),
        make_account("paper-fo", "paper", "auto", asset="fo", acnt_prdt_cd="03"),
    ]


def _client(project, accounts, now=NOW):
    ports = {"accounts": AccountsPortImpl(accounts, Secrets())}
    return make_web_client(project, [SPOT, FO], ports=ports, now=now)


@pytest.fixture
def client(project, accounts):
    return _client(project, accounts)


@pytest.fixture
def ledger(project, client):
    c = db.connect(project / "state" / "trader.db")
    try:
        yield c
    finally:
        c.close()


def _decide(conn, ticker="005930", d=D, market="KR", at=None, **over) -> int:
    ingest_checkpoint(conn, d, market, ticker, "30_decision",
                      {"data": ok_decision(ticker=ticker, **over)})  # fmt: skip
    did = xledger.decision_id(conn, d, market, ticker)
    if at is not None:
        conn.execute("UPDATE decisions SET created_at=? WHERE id=?", (at, did))
        conn.commit()
    return did


def _order(conn, *, decision_id=None, account="paper-main", ticker="005930", market="KR",
           status="accepted", origin=None, at="2026-11-04T01:00:00+00:00", side="BUY", qty=3):
    return xledger.insert_order(
        conn, decision_id=decision_id, account_id=account, ticker=ticker, market=market,
        side=side, qty=qty, order_type="LIMIT", price=Decimal("71000"), status=status,
        order_no="0001", message="", mode="auto", created_at=at, origin=origin,
    )  # fmt: skip


def _write_run(state, ticker="005930", d=D):
    rd = run_store.run_dir(d, "KR", ticker, state)
    run_store.write_checkpoint(rd, "00_inputs", {"meta": {"ticker": ticker}, "technical": {}})
    run_store.write_checkpoint(
        rd, "10_analyst_technical", {"schema_version": 1, "data": {"signal": "BUY"}, "call": None}
    )
    run_store.write_checkpoint(
        rd, "20_debate_r0_bull", {"schema_version": 1, "data": ok_turn("bull"), "call": None}
    )
    return rd


# --- 시장 today·체크포인트 (옛 test_today_*) ---------------------------------------------


def test_today_and_checkpoint_read(client, ledger, project):
    state = project / "state"
    _write_run(state)
    mdir = run_store.market_dir(D, "KR", state)
    run_store.write_checkpoint(
        mdir, "_scan", {"picks": [{"ticker": "005930", "reason": "돌파"}], "analyze_targets": [1]}
    )
    did = _decide(ledger)
    r = client.get("/api/markets/KR/today", params={"date": D.isoformat()}).json()
    assert r["tickers"]["005930"] == ["00_inputs", "10_analyst_technical", "20_debate_r0_bull"]
    assert r["market_files"] == ["_scan"]
    assert r["scan"]["picks"][0] == {"ticker": "005930", "reason": "돌파", "discovery": None}
    assert r["scan"]["targets"] == 1
    assert client.get("/api/markets/KR/today").json()["date"] == D.isoformat()  # now=KST 15시
    m = client.get("/api/markets/KR/today", params={"stage": "_scan"}).json()
    assert m["stage"] == "_scan" and m["data"]["picks"][0]["ticker"] == "005930"
    assert client.get("/api/markets/KR/today", params={"stage": "_screen"}).status_code == 404
    assert client.get("/api/markets/KR/today", params={"stage": "../x"}).status_code == 400
    cp = client.get(f"/api/decisions/spot/{did}/checkpoints/10_analyst_technical").json()
    assert cp["stage"] == "10_analyst_technical" and cp["data"]["signal"] == "BUY"
    assert client.get(f"/api/decisions/spot/{did}/checkpoints/30_decision").status_code == 404
    assert client.get("/api/markets/XX/today").status_code == 400


def test_today_hides_empty_dirs_and_read_does_not_create(client, ledger, project):
    state = project / "state"
    _write_run(state)
    (run_store.run_dir(D, "KR", "000660", state).parent / "_").mkdir(exist_ok=True)
    tickers = client.get("/api/markets/KR/today", params={"date": D.isoformat()}).json()["tickers"]
    assert "_" not in tickers and "000660" not in tickers
    did = _decide(ledger, ticker="999999")
    r = client.get(f"/api/decisions/spot/{did}/checkpoints/00_inputs")
    assert r.status_code == 404
    assert not (state / "runs" / D.isoformat() / "KR" / "999999").exists()
    assert client.get(f"/api/decisions/spot/{did}/checkpoints/00_inputs.json").status_code == 400
    assert client.get("/api/decisions/spot/9999/checkpoints/00_inputs").status_code == 404
    assert client.get("/api/markets/KR/today", params={"date": "nope"}).status_code == 400


def test_today_default_date_is_market_run_date(project, accounts):
    """US 는 KST 자정 뒤에도 전날 뉴욕 운영일 디렉터리에 실행이 있어 기본 날짜는 run_date_for."""
    c = _client(project, accounts, now=datetime(2026, 10, 9, 23, 0, tzinfo=UTC))
    assert c.get("/api/markets/US/today").json()["date"] == "2026-10-09"
    assert c.get("/api/markets/KR/today").json()["date"] == "2026-10-10"


# --- 결정 상세·trace·outcomes --------------------------------------------------------------


def test_decision_trace_endpoint(client, ledger, project):
    did = _decide(ledger)
    r = client.get(f"/api/decisions/spot/{did}/trace")
    assert r.status_code == 200 and r.json()["decision"]["id"] == did
    assert r.json()["analysts"] == [] and r.json()["stages"] == []
    assert client.get("/api/decisions/spot/999/trace").status_code == 404
    # 리포트·토론·판정이 쌓이면 표에서 읽는다(체크포인트 목록은 run 디렉터리)
    ingest_checkpoint(ledger, D, "KR", "005930", "10_analyst_technical", {"data": ok_report()})
    ingest_checkpoint(ledger, D, "KR", "005930", "20_debate_r0_bull", {"data": ok_turn("bull")})
    ingest_checkpoint(ledger, D, "KR", "005930", "20_debate_r0_bear", {"data": ok_turn("bear")})
    xledger.insert_risk_check(ledger, decision_id=did, account_id="paper-main", approved=False,
                              adjusted_qty=None, reasons=["주간 한도"])  # fmt: skip
    _write_run(project / "state")
    tr = client.get(f"/api/decisions/spot/{did}/trace").json()
    (a,) = tr["analysts"]
    assert a["role"] == "technical" and a["label"] == "차트" and a["signal"] == "BUY"
    assert a["evidence"] == ["종가 > SMA20"]
    assert [(t["round"], t["side"]) for t in tr["debate"]] == [(0, "bear"), (0, "bull")]
    (g,) = tr["gate"]
    assert g["account_id"] == "paper-main" and g["approved"] is False
    assert g["reasons"] == ["주간 한도"]
    assert tr["stages"] == ["00_inputs", "10_analyst_technical", "20_debate_r0_bull"]


def test_decision_detail_rounds_summary_and_outcomes(client, ledger):
    did = _decide(ledger, rationale="첫 줄\n둘째 줄")
    ingest_checkpoint(ledger, D, "KR", "005930", "31_decision_r1",
                      {"data": ok_decision(action="HOLD", stop_loss=66000.0)})  # fmt: skip
    body = client.get(f"/api/decisions/spot/{did}").json()
    assert body["decision"]["id"] == did and body["decision"]["action"] == "HOLD"
    assert [r["round"] for r in body["rounds"]] == [0, 1]
    assert body["rounds"][1]["data"]["stop_loss"] == 66000.0
    assert body["summary"] == "검증가능한 근거 요약"
    assert body["news_refs"] == []
    assert client.get("/api/decisions/spot/999").status_code == 404
    xledger.insert_risk_check(ledger, decision_id=did, account_id="paper-main", approved=True,
                              adjusted_qty=3, reasons=[])  # fmt: skip
    _order(ledger, decision_id=did, status="filled")
    out = client.get(f"/api/decisions/spot/{did}/outcomes").json()
    assert out["decision"]["id"] == did
    (o,) = out["outcomes"]
    assert o["account_id"] == "paper-main" and o["approved"] is True and o["adjusted_qty"] == 3
    assert o["order_status"] == "filled" and o["order_qty"] == 3
    assert client.get("/api/decisions/spot/999/outcomes").status_code == 404


def test_news_refs_from_analyst_evidence(client, ledger):
    """evidence `source_id` 가 `news:<id>` 인 것만 센다(`market_notes` 등은 무시)."""
    did = _decide(ledger)
    other = _decide(ledger, ticker="000660", d=date(2026, 11, 3))  # 다른 날 — 지도에 없음
    ingest_checkpoint(ledger, D, "KR", "005930", "10_analyst_news_macro", {"data": ok_report(
        role="news_macro", evidence=[{"claim": "수주", "source_id": "news:9"},
                                     {"claim": "시장 메모", "source_id": "market_notes"}],
    )})  # fmt: skip
    ingest_checkpoint(ledger, D, "KR", "005930", "10_analyst_sentiment", {"data": ok_report(
        role="sentiment", evidence=[{"claim": "호평", "source_id": "news:7"},
                                    {"claim": "중복", "source_id": "news:9"}],
    )})  # fmt: skip
    ingest_checkpoint(ledger, date(2026, 11, 3), "KR", "000660", "10_analyst_sentiment",
                      {"data": ok_report(role="sentiment", ticker="000660",
                                         evidence=[{"claim": "x", "source_id": "news:5"}])})
    r = client.get("/api/decisions/spot/news-refs", params={"date": D.isoformat()})
    assert r.status_code == 200 and r.json() == {"7": [did], "9": [did]}
    assert client.get(f"/api/decisions/spot/{did}").json()["news_refs"] == [7, 9]
    assert client.get(f"/api/decisions/spot/{other}").json()["news_refs"] == [5]
    assert client.get("/api/decisions/spot/news-refs").json() == {"7": [did], "9": [did]}


# --- merge 조각 ---------------------------------------------------------------------------


def test_decisions_merged_spot_and_fo_by_date(client, ledger):
    """같은 거래일의 현물·파생 결정이 한 목록에 `at` 역순으로. 다른 날은 빠진다."""
    early = _decide(ledger, ticker="000660", at="2026-11-03T23:30:00+00:00")  # KST 08:30
    late = _decide(ledger, ticker="005930", at="2026-11-04T01:00:00+00:00")
    _decide(ledger, ticker="035420", d=date(2026, 11, 3), at="2026-11-03T01:00:00+00:00")
    ledger.execute(
        "INSERT INTO fo_decisions(date, account_id, position_id, round, request_id, intent, "
        "direction, vehicle, data, created_at) VALUES "
        "(?, NULL, NULL, 0, 'rq1', 'ENTER', 'LONG', 'FUTURES', '{}', ?)",
        (D.isoformat(), "2026-11-04T00:10:00+09:00"),  # 2026-11-03T15:10Z — 가장 이른 행
    )
    ledger.commit()
    body = client.get("/api/decisions").json()
    assert body["partial"] is False
    assert [(x["asset"], x["id"]) for x in body["items"]] == [
        ("spot", late), ("spot", early), ("fo", 1)
    ]
    spot = body["items"][0]
    assert spot["lane"] == "spot" and spot["market"] == "KR" and spot["ticker"] == "005930"
    assert spot["side"] == "BUY" and spot["date"] == D.isoformat()
    assert {"qty", "status", "account_id", "at"} <= set(spot)
    fo = body["items"][2]
    assert fo["intent"] == "ENTER" and fo["date"] == D.isoformat()
    old = client.get("/api/decisions", params={"date": "2026-11-03"}).json()["items"]
    assert [x["ticker"] for x in old if x["asset"] == "spot"] == ["035420"]
    assert client.get("/api/decisions", params={"market": "US"}).json()["items"] == []  # fo 는 KR
    kr = client.get("/api/decisions", params={"market": "KR"}).json()["items"]
    assert [x["id"] for x in kr] == [late, early, 1]


def test_decision_rows_carry_order_state_and_scalp_lane(client, ledger):
    """결정 행의 qty·status·account_id 는 그 결정의 마지막 주문(없으면 승인 대기·판정 거부).
    단타(origin=scalp)는 결정 행이 없으므로 주문에서 `lane: scalp` 행을 만든다."""
    filled = _decide(ledger, ticker="005930")
    blocked = _decide(ledger, ticker="000660")
    hold = _decide(ledger, ticker="035420", action="HOLD")
    _order(ledger, decision_id=filled, status="filled", qty=5)
    xledger.insert_risk_check(ledger, decision_id=blocked, account_id="paper-main",
                              approved=False, adjusted_qty=None, reasons=["x"])  # fmt: skip
    sid = _order(ledger, ticker="111111", origin="scalp", status="accepted", qty=2,
                 at="2026-11-04T02:00:00+00:00")
    rows = {x["ticker"]: x for x in client.get("/api/decisions").json()["items"]}
    assert rows["005930"]["status"] == "filled" and rows["005930"]["qty"] == 5
    assert rows["005930"]["account_id"] == "paper-main"
    assert rows["000660"]["status"] == "blocked" and rows["000660"]["account_id"] == "paper-main"
    assert rows["035420"]["status"] is None and rows["035420"]["side"] is None
    assert rows["035420"]["id"] == hold
    sc = rows["111111"]
    assert sc["lane"] == "scalp" and sc["id"] == f"scalp-{sid}" and sc["order_id"] == sid
    assert sc["side"] == "BUY" and sc["qty"] == 2 and sc["status"] == "accepted"
    mine = client.get("/api/decisions", params={"account_id": "real-main"}).json()["items"]
    assert mine == []  # 현물 계좌 필터에 파생 아침 결정(account_id NULL)도 섞이지 않는다


def test_orders_isolated_per_account(client, ledger):
    did = _decide(ledger)
    oid = _order(ledger, decision_id=did)
    _order(ledger, status="filled", origin="scalp", at="2026-11-04T02:00:00+00:00")
    ledger.execute(
        "INSERT INTO fills(order_id, account_id, qty, price, filled_at) VALUES (?,?,?,?,?)",
        (oid, "paper-main", 1, "71000", "2026-11-04T01:05:00+00:00"),
    )
    ledger.commit()
    items = client.get("/api/orders", params={"account_id": "paper-main"}).json()["items"]
    assert len(items) == 2
    scalp, llm = items  # at 역순
    assert scalp["lane"] == "scalp" and scalp["origin"] == "scalp"
    assert llm["lane"] == "spot" and llm["origin"] == "llm" and llm["decision_id"] == did
    assert llm["asset"] == "spot" and llm["price"] == "71000"
    assert llm["fills"] == [
        {"qty": 1, "price": "71000", "filled_at": "2026-11-04T01:05:00+00:00"}
    ]
    assert client.get("/api/orders", params={"account_id": "real-main"}).json()["items"] == []
    assert client.get("/api/risk-checks", params={"account_id": "real-main"}).json() == []


def test_orders_filter_by_date_and_status(client, ledger):
    _order(ledger, status="filled")
    _order(ledger, status="accepted", at="2026-11-04T03:00:00+00:00")
    _order(ledger, status="filled", at="2026-11-02T03:00:00+00:00")  # 다른 날
    # 파생 주문 행(asset='fo')은 fo 조각이 다리로 보여 준다 — 현물 조각에 섞이지 않는다
    xledger.insert_order(
        ledger, decision_id=None, account_id="paper-fo", ticker="105W11", market="KR",
        side="BUY", qty=1, order_type="LIMIT", price=Decimal("350"), status="accepted",
        order_no="9", message="", mode="auto", created_at="2026-11-04T01:30:00+00:00",
        asset="fo", origin="fo",
    )  # fmt: skip
    items = client.get("/api/orders").json()["items"]
    assert [x["status"] for x in items] == ["accepted", "filled"]
    assert [x["status"] for x in client.get(
        "/api/orders", params={"status": "filled"}).json()["items"]] == ["filled"]  # fmt: skip
    old = client.get("/api/orders", params={"date": "2026-11-02"}).json()["items"]
    assert [x["at"] for x in old] == ["2026-11-02T03:00:00+00:00"]


def test_risk_checks_by_date(client, ledger):
    did = _decide(ledger)
    xledger.insert_risk_check(ledger, decision_id=did, account_id="paper-main", approved=False,
                              adjusted_qty=None, reasons=["주간 한도"])  # fmt: skip
    old = _decide(ledger, ticker="000660", d=date(2026, 11, 3))
    xledger.insert_risk_check(ledger, decision_id=old, account_id="paper-main", approved=True,
                              adjusted_qty=1, reasons=[])  # fmt: skip
    (rc,) = client.get("/api/risk-checks").json()
    assert rc["decision_id"] == did and rc["approved"] is False and rc["reasons"] == ["주간 한도"]
    assert rc["ticker"] == "005930" and rc["market"] == "KR" and rc["date"] == D.isoformat()
    (rc2,) = client.get("/api/risk-checks", params={"date": "2026-11-03"}).json()
    assert rc2["decision_id"] == old and rc2["approved"] is True


def test_risk_checks_older_date_beyond_limit(client, ledger, monkeypatch):
    """날짜 조건은 SQL 에서 먼저 건다 — 더 최근 판정이 상한보다 많아도 옛 날짜 판정이 나온다."""
    from trader.features.spot import decisions_view

    monkeypatch.setattr(decisions_view, "RISK_CHECKS_LIMIT", 3)
    old = _decide(ledger, ticker="000660", d=date(2026, 11, 3))
    xledger.insert_risk_check(ledger, decision_id=old, account_id="paper-main", approved=True,
                              adjusted_qty=1, reasons=[])  # fmt: skip
    did = _decide(ledger)
    for _ in range(5):
        xledger.insert_risk_check(ledger, decision_id=did, account_id="paper-main",
                                  approved=False, adjusted_qty=None, reasons=["x"])  # fmt: skip
    (rc,) = client.get("/api/risk-checks", params={"date": "2026-11-03"}).json()
    assert rc["decision_id"] == old
    assert len(client.get("/api/risk-checks").json()) == 3  # 상한은 날짜 조건 뒤에


def test_csv_format_for_orders_decisions(client, ledger):
    """`?format=csv` 는 같은 표를 CSV(BOM) 로. 기본은 JSON."""
    _decide(ledger)
    _order(ledger)
    r = client.get("/api/orders", params={"account_id": "paper-main", "format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.startswith("﻿") and "attachment" in r.headers["content-disposition"]
    r2 = client.get("/api/decisions", params={"format": "csv"})
    lines = r2.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("asset,id,date,market,ticker,side") and "005930" in lines[1]
    assert isinstance(client.get("/api/decisions").json()["items"], list)


# --- SSE·nav ------------------------------------------------------------------------------


def _fp(client, key):
    deps = client.app.state.deps
    return next(k for k in spot_routes.SSE_KEYS if k.key == key).probe(deps)


def test_sse_snapshot_detects_status_changes_without_new_rows(client, ledger):
    """MAX(id) 만 보면 체결·만료 같은 상태 변경(UPDATE)을 못 잡는다 → 건수·최대 id·상태별 건수."""
    oid = _order(ledger)
    s0 = _fp(client, "orders")
    xledger.set_order_status(ledger, oid, "filled")
    ledger.commit()
    s1 = _fp(client, "orders")
    assert s1 != s0
    ledger.execute(
        "INSERT INTO fills (order_id, account_id, qty, price, filled_at)"
        " VALUES (?,'paper-main',1,'100','t')", (oid,)
    )
    ledger.commit()
    s2 = _fp(client, "orders")
    assert s2 != s1  # 체결 적재도 주문 표 재조회 대상
    assert _fp(client, "orders") == s2  # 바뀐 게 없으면 같은 지문
    t0 = _fp(client, "triggers")
    tid = xledger.insert_trigger(ledger, market="KR", ticker="005930", kind="stop", detail={},
                                 price=None, decision_id=None, account_id=None,
                                 created_at="2026-11-04T01:00:00+00:00")  # fmt: skip
    t1 = _fp(client, "triggers")
    assert t1 != t0
    xledger.mark_trigger_handled(ledger, tid, "s1", handled_at="2026-11-04T01:01:00+00:00")
    assert _fp(client, "triggers") != t1


def test_sse_decisions_fingerprint_sees_same_day_update(client, ledger):
    """같은 날 재평가가 결정 행의 json 만 바꿔도(손절 변경, 주문 없음) decisions 지문이 바뀐다."""
    base = {"ticker": "005930", "action": "HOLD", "target_weight": 0.1, "confidence": 0.6}
    ingest_checkpoint(ledger, D, "KR", "005930", "30_decision",
                      {"data": {**base, "stop_loss": 90.0}, "call": {}})  # fmt: skip
    s0 = _fp(client, "decisions")
    ingest_checkpoint(ledger, D, "KR", "005930", "31_decision_r1",
                      {"data": {**base, "stop_loss": 95.0}, "call": {}})  # fmt: skip
    s1 = _fp(client, "decisions")
    assert s1 != s0
    assert _fp(client, "decisions") == s1


def test_spot_nav_has_lane_item():
    assert NavItem("today", "spot-lane", "현물", 15) in SPOT.nav
    assert {k.key for k in SPOT.sse_keys} >= {"decisions", "orders", "triggers"}
