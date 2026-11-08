"""fo 웹 조회: `/accounts/{id}/positions` fo 조각·`/fo/legs`·`/fo/summary`·`/fo/today`·SSE `fo`.

옛 `tests/web/test_fo_api.py` 중 조회 건(positions 는 계좌 필수·today/legs 모양·summary 상태와
잔고·실전 off·today 게이트 상세·다리 갱신 지문) 이식. `account`·`scorecard`·`report` 는 Task 11."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from tests.features.accounts.conftest import make_account
from tests.features.conftest import web_client as make_web_client
from tests.features.fo.conftest import ACCT, NOW, TODAY
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.fo import executor, store
from trader.features.fo.feature import FEATURE
from trader.kernel.broker.fo import CONTRACT_VERSION
from trader.kernel.config.secrets import Secrets
from trader.kernel.ledger import control, db
from trader.kernel.registry.types import NavItem
from trader.kernel.session import run_store


def _fo_account(env="paper", mode="auto"):
    return make_account(ACCT if env == "paper" else "real-fo", env, mode, ["KR"],
                        acnt_prdt_cd="03", asset="fo")  # fmt: skip


def _client(project, fo_accounts, *, enabled=False):
    accounts = [make_account("paper-main", "paper", "auto"), *fo_accounts]
    c = make_web_client(
        project, [FEATURE], ports={"accounts": AccountsPortImpl(accounts, Secrets())}, now=NOW
    )
    deps = c.app.state.deps
    s = deps.settings
    s = s.model_copy(update={"fo": s.fo.model_copy(update={"enabled": enabled})})
    c.app.state.deps = dataclasses.replace(deps, settings=s)
    return c


@pytest.fixture
def client(project):
    return _client(project, [_fo_account()])


@pytest.fixture
def ledger(project, client):
    c = db.connect(project / "state" / "trader.db")
    try:
        yield c
    finally:
        c.close()


def _position(conn, **over):
    fields = dict(
        account_id=ACCT, code="105W11", kind="FUTURES", contract_side="LONG", direction="LONG",
        vehicle="FUTURES", status="held", qty_target=2, qty_held=2, leverage_used=0.35,
        stop_pt=Decimal("345"), nav_at_open=Decimal("100000000"), decision_date=TODAY.isoformat(),
        opened_at="2026-10-27T00:30:00+00:00",
    )  # fmt: skip
    fields.update(over)
    with store.tx(conn):
        pid = store.insert_position(conn, **fields)
    return store.get_position(conn, pid)


def _snapshot(conn, code="105W11", avg="350", kind="FUTURES"):
    store.write_positions_snapshot(conn, account_id=ACCT, date=TODAY.isoformat(), rows=[
        {"code": code, "kind": kind, "contract_side": "LONG", "qty": 2, "avg": Decimal(avg),
         "current": Decimal(avg), "eval": None},
    ])  # fmt: skip
    conn.commit()


def _items(client, acct):
    body = client.get(f"/api/accounts/{acct}/positions").json()
    assert body["partial"] is False
    return body["items"]


def test_fo_positions_requires_account_id(client, ledger):
    """fo 조각은 파생 계좌에만 행을 낸다: 현물 계좌·모르는 계좌는 빈 목록."""
    _position(ledger)
    _snapshot(ledger)
    assert _items(client, "paper-main") == []
    assert _items(client, "nobody") == []
    (row,) = _items(client, ACCT)
    assert row["asset"] == "fo" and row["lane"] == "fo" and row["ticker"] == "105W11"
    assert row["qty"] == 2 and row["avg_price"] == "350" and row["currency"] == "KRW"
    assert row["krw_value"] == "35000000"  # 350pt × 2계약 × 승수 50,000
    assert row["last_price"] is None and row["at"] == "2026-10-27T00:30:00+00:00"
    assert row["stop"] == {"level": "345", "source": "손절 −1.4%"}
    assert row["target"] is None
    assert row["direction"] == "LONG" and row["status"] == "held"


def test_fo_rows_stop_kinds(client, ledger):
    """선물 가격 손절(stop_fut)·옵션 프리미엄 보호선이 평단 기준 −N%, 손절이 없으면 만기 청산 D-n.
    익절선은 결정(tp_pt)."""
    _position(ledger, stop_fut=Decimal("343"), tp_pt=Decimal("360"))
    (row,) = _items(client, ACCT)
    assert row["stop"]["level"] == "343"
    assert row["stop"]["source"].startswith("손절")  # 평단 없음 — 비율 생략
    assert row["target"] == {"level": "360", "source": "결정"}
    ledger.execute("DELETE FROM fo_positions")
    ledger.commit()
    _position(ledger, code="205W11350", kind="CALL", vehicle="OPTION_BUY", stop_pt=None,
              premium_stop=Decimal("2.5"))  # fmt: skip
    _snapshot(ledger, code="205W11350", avg="5", kind="CALL")
    (row,) = _items(client, ACCT)
    assert row["stop"] == {"level": "2.5", "source": "손절 −50.0%"}
    ledger.execute("DELETE FROM fo_positions")
    ledger.commit()
    # 최종거래일 11/12(목) − roll_days 2 운영일 = 강제 청산 11/10(화), 오늘 10/27 → D-14
    _position(ledger, stop_pt=None, last_trade_date="2026-11-12")
    (row,) = _items(client, ACCT)
    assert row["stop"] == {"level": None, "source": "만기 D-14"}


def test_fo_legs_requires_account_id(client):
    assert client.get("/api/fo/legs").status_code == 422
    assert client.get("/api/fo/legs", params={"account_id": "paper-main"}).status_code == 400


def test_fo_bad_date_is_400_not_500(client):
    """잘못된 `date` 는 400(M2) — 단독 라우트와 합친 자원 조각 모두."""
    bad = {"date": "2026-13-40"}
    assert client.get("/api/fo/today", params=bad).status_code == 400
    r = client.get("/api/fo/legs", params={"account_id": ACCT, **bad})
    assert r.status_code == 400 and "date" in r.json()["detail"]
    assert client.get("/api/decisions", params=bad).status_code == 400
    assert client.get("/api/orders", params=bad).status_code == 400


def test_fo_today_and_legs_shapes(project, client, ledger):
    pos = _position(ledger)
    with store.tx(ledger):
        leg_id = store.insert_leg(ledger, position_id=pos.id, account_id=ACCT, intent="OPEN",
                                  round=0, attempt=1, qty=2, price=None, order_type="MARKET")
        store.set_leg(ledger, leg_id, status="accepted", order_no="X1")
    store.record_decision(ledger, date="2026-10-27",
                          account_id=None, position_id=None, round=0, request_id="r1",
                          intent="OPEN", direction="LONG", vehicle="FUTURES",
                          target_leverage=1.0, confidence=0.7, index_at_decision="350",
                          quote_asof=None, data='{"decision": {"stop_pt": 345.0}}')  # fmt: skip
    ledger.commit()
    mdir = run_store.market_dir(TODAY, "KR", project / "state", create=True)
    run_store.write_checkpoint(mdir, f"_fo_risk_check_{ACCT}",
                               run_store.checkpoint_payload({"verdict": {"approved": True}}, None))
    today = client.get("/api/fo/today", params={"date": "2026-10-27"}).json()
    assert today["decision"]["intent"] == "OPEN" and today["decision"]["stop_pt"] == 345.0
    assert today["reevals"] == [] and today["accounts"][0]["id"] == ACCT
    assert f"_fo_risk_check_{ACCT}" in today["checks"]
    legs = client.get("/api/fo/legs", params={"account_id": ACCT, "date": "2026-10-27"}).json()
    assert legs and legs[0]["intent"] == "OPEN" and legs[0]["status"] == "accepted"
    assert legs[0]["code"] == "105W11" and legs[0]["order_no"] == "X1"


def test_fo_today_checks_include_gate_detail(project, client):
    mdir = run_store.market_dir(TODAY, "KR", project / "state", create=True)
    verdict = {"approved": True, "reasons": [], "note": "", "leverage_used": 0.4,
               "exposure_after": "20000000", "stress_loss_after": "900000",
               "lifetime_budget": {"used": "0", "planned": "300000", "limit": "1000000",
                                   "free": "700000"},
               "cross_factor": 1.0, "n_target": 2, "approved_mult": None}  # fmt: skip
    run_store.write_checkpoint(
        mdir, f"_fo_risk_check_{ACCT}", run_store.checkpoint_payload({"verdict": verdict}, None)
    )
    chk = client.get("/api/fo/today").json()["checks"][f"_fo_risk_check_{ACCT}"]
    assert chk["leverage_used"] == 0.4 and chk["n_target"] == 2
    assert chk["lifetime_budget"]["limit"] == "1000000" and chk["stress_loss_after"] == "900000"


def _nav(conn, acct=ACCT):
    store.write_nav_snapshot(
        conn, account_id=acct, date=TODAY.isoformat(), nav=Decimal("50000000"),
        cash=Decimal("40000000"), margin_total=Decimal("10000000"), add_margin=Decimal("0"),
    )  # fmt: skip
    conn.commit()


def test_fo_summary_states_and_balance(project, ledger):
    """상태 우선순위: 꺼짐(disabled) → 검증 전(unverified) → active → 일일 손실(halted).
    NAV·증거금 비율은 `fo_nav_snapshot`(accounts 통합 현황은 account_nav 만 읽는다 — T6-R2)."""
    _nav(ledger)
    c = _client(project, [_fo_account()])
    row = c.get("/api/fo/summary").json()["accounts"][0]
    assert row["id"] == ACCT and row["state"] == "disabled" and row["mode"] == "auto"
    assert row["nav"] == "50000000" and row["margin_pct"] == 0.2
    assert row["verified"] == {"FUTURES": False, "OPTION_BUY": False, "OPTION_SELL": False}
    c = _client(project, [_fo_account()], enabled=True)
    assert c.get("/api/fo/summary").json()["accounts"][0]["state"] == "unverified"
    deps = c.app.state.deps
    control.set_flag(ledger, executor.verified_key(deps.settings, _fo_account(), "FUTURES"),
                     CONTRACT_VERSION)  # fmt: skip
    ledger.commit()
    row = c.get("/api/fo/summary").json()["accounts"][0]
    assert row["state"] == "active" and row["verified"]["FUTURES"] is True
    control.set_flag(ledger, f"fo_halted:{ACCT}", TODAY.isoformat())
    ledger.commit()
    assert c.get("/api/fo/summary").json()["accounts"][0]["state"] == "halted"
    _position(ledger)
    pos = c.get("/api/fo/summary").json()["accounts"][0]["positions"]
    assert pos == [{"code": "105W11", "kind": "FUTURES", "contract_side": "LONG",
                    "direction": "LONG", "qty": 2, "status": "held"}]  # fmt: skip


def test_fo_summary_paused_is_entries_paused_not_record_only(project, ledger):
    """전체 일시정지 + 모드 auto 는 위험 청산이 전송되므로 record_only 가 아니라 entries_paused;
    모드가 auto 가 아니면 일시정지와 무관하게 record_only."""
    c = _client(project, [_fo_account()], enabled=True)
    deps = c.app.state.deps
    control.set_flag(ledger, executor.verified_key(deps.settings, _fo_account(), "FUTURES"),
                     CONTRACT_VERSION)  # fmt: skip
    ledger.commit()
    assert c.get("/api/fo/summary").json()["accounts"][0]["state"] == "active"
    control.set_flag(ledger, "paused", True)
    ledger.commit()
    row = c.get("/api/fo/summary").json()["accounts"][0]
    assert row["state"] == "entries_paused" and row["paused"] is True
    c2 = _client(project, [_fo_account(mode="off")], enabled=True)
    row = c2.get("/api/fo/summary").json()["accounts"][0]
    assert row["state"] == "record_only"

def test_fo_summary_real_account_is_off_and_empty_without_fo(project):
    c = _client(project, [_fo_account(env="real", mode="approve")], enabled=True)
    row = c.get("/api/fo/summary").json()["accounts"][0]
    assert row["state"] == "off" and row["mode"] == "off"
    assert _client(project, []).get("/api/fo/summary").json() == {"accounts": []}


def test_sse_fingerprint_changes_on_leg_update(client, ledger):
    (key,) = [k for k in FEATURE.sse_keys if k.key == "fo"]
    deps = client.app.state.deps
    pos = _position(ledger)
    before = key.probe(deps)
    with store.tx(ledger):
        leg_id = store.insert_leg(ledger, position_id=pos.id, account_id=ACCT, intent="CLOSE",
                                  round=0, attempt=1, qty=2, price=None, order_type="MARKET")
    mid = key.probe(deps)
    with store.tx(ledger):
        store.set_leg(ledger, leg_id, status="accepted", order_no="X1")
    after = key.probe(deps)
    assert before != mid != after


def test_fo_nav_items():
    assert NavItem("accounts", "fo-positions", "파생 계좌", 20) in FEATURE.nav
    assert NavItem("today", "fo-lane", "파생", 30) in FEATURE.nav
    assert NavItem("system", "fo", "파생", 60) in FEATURE.nav  # 시스템 화면 「파생」 절
