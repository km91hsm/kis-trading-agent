"""fo 「성과」·「시스템」 웹: `/analytics/fo/scorecard|report|summary`·`/system/fo`.

옛 `tests/web/test_fo_api.py` 의 `test_fo_scorecard_and_report`·
`test_fo_account_context_and_triggers` 이식. `/system/fo` 는 옛 `/fo/account` 를 파생 계좌
전체(id 만)로 넓히고 control 플래그 7종 요약을 더한다(T11-R1)."""

from __future__ import annotations

from decimal import Decimal

from tests.features.fo.conftest import ACCT, NOW, TODAY
from tests.features.fo.test_routes import _client, _fo_account, _position
from trader.features.fo import executor, store
from trader.kernel.broker.fo import CONTRACT_VERSION
from trader.kernel.ledger import control, db


def _ledger(project):
    return db.connect(project / "state" / "trader.db")


def _decision(conn, *, direction="LONG", hit=None, pnl=None, matured=False, round_=0):
    did = store.record_decision(
        conn, date=TODAY.isoformat(), account_id=None, position_id=None, round=round_,
        request_id=f"r{round_}", intent="OPEN", direction=direction, vehicle="FUTURES",
        target_leverage=1.0, confidence=0.7, index_at_decision="350", quote_asof=None, data="{}",
    )  # fmt: skip
    if matured:  # 채점 열은 성숙 잡이 채운다 — 테스트는 직접
        conn.execute(
            "UPDATE fo_decisions SET matured_at = ?, direction_hit = ?, contract_pnl = ? "
            "WHERE id = ?",
            ("2026-10-30T00:00:00+00:00", hit, pnl, did),
        )
    conn.commit()


def test_fo_scorecard_and_report(project):
    c = _client(project, [_fo_account()])
    conn = _ledger(project)
    try:
        assert c.get("/api/analytics/fo/scorecard").json() == {
            "decisions": 0, "matured": 0, "hit_rate": None, "contract_pnl": "0",
        }  # fmt: skip
        _decision(conn, hit=1, pnl="150000", matured=True)
        _decision(conn, hit=0, pnl="-50000", matured=True, round_=1)
        _decision(conn, direction="FLAT", matured=True, round_=2)  # FLAT 은 채점 밖
        sc = c.get("/api/analytics/fo/scorecard").json()
        assert sc == {"decisions": 3, "matured": 2, "hit_rate": 0.5, "contract_pnl": "100000"}
        for d, nav in (("2026-10-26", "100"), ("2026-10-27", "110")):
            store.write_nav_snapshot(conn, account_id=ACCT, date=d, nav=Decimal(nav),
                                     cash=Decimal("50"), margin_total=Decimal(0),
                                     add_margin=Decimal(0))  # fmt: skip
        conn.commit()
    finally:
        conn.close()
    rep = c.get("/api/analytics/fo/report", params={"account_id": ACCT, "days": 30}).json()
    assert rep["account_id"] == ACCT and rep["days"] == 30
    assert [x["nav"] for x in rep["nav"]] == ["100", "110"] and abs(rep["return"] - 0.1) < 1e-9
    assert c.get("/api/analytics/fo/report").status_code == 422
    assert c.get("/api/analytics/fo/report", params={"account_id": "paper-main"}).status_code == 400
    assert c.get(
        "/api/analytics/fo/report", params={"account_id": ACCT, "days": 0}
    ).status_code == 422


def test_fo_analytics_summary_matches_fo_summary(project):
    c = _client(project, [_fo_account()])
    assert c.get("/api/analytics/fo/summary").json() == c.get("/api/fo/summary").json()
    assert c.get("/api/analytics/fo/summary").json()["accounts"][0]["id"] == ACCT


def test_system_fo_context_triggers_and_flags(project):
    """파생 계좌마다 상태 + 오늘 트리거 + control 플래그 7종 요약, 시장·한도 맥락은 오늘
    `_fo_inputs` 가 없으면 설정값. 계좌번호는 내보내지 않는다(id 만)."""
    c = _client(project, [_fo_account()], enabled=True)
    deps = c.app.state.deps
    conn = _ledger(project)
    try:
        pos = _position(conn)
        conn.execute(
            "INSERT INTO triggers(account_id, market, ticker, kind, detail, price, created_at) "
            "VALUES (?, 'KR', '105W11', 'fo:move', '{}', '350', ?)",
            (ACCT, NOW.isoformat()),
        )
        conn.commit()
        r = c.get("/api/system/fo").json()
        (a,) = r["accounts"]
        assert a["id"] == ACCT and a["env"] == "paper" and a["state"] == "unverified"
        assert [t["kind"] for t in a["triggers"]] == ["move"]
        assert a["flags"] == {
            "fo_verified": {"FUTURES": False, "OPTION_BUY": False, "OPTION_SELL": False},
            "fo_halted": False, "fo_day_open": None, "fo_alerts": 0, "fo_ref_iv": {},
            "fo_pending_entry": False, "fo_replace": [],
        }  # fmt: skip
        lim = r["context"]["limits"]
        assert lim["max_leverage"] == deps.settings.fo.max_leverage and lim["enabled"] is True
        assert r["context"]["source"] == "settings"
        control.set_flag(conn, executor.verified_key(deps.settings, _fo_account(), "FUTURES"),
                         CONTRACT_VERSION)  # fmt: skip
        control.set_flag(conn, executor.halted_key(ACCT), TODAY.isoformat())
        control.set_flag(conn, f"fo_day_open:{ACCT}", {"date": TODAY.isoformat(),
                                                       "unrealized": "0"})  # fmt: skip
        control.set_flag(conn, f"fo_alerts:{ACCT}", {"date": TODAY.isoformat(),
                                                    "keys": ["a", "b"]})  # fmt: skip
        control.set_flag(conn, f"fo_ref_iv:{pos.id}", 0.21)
        control.set_flag(conn, f"fo_pending_entry:{ACCT}", {"date": TODAY.isoformat()})
        control.set_flag(conn, f"fo_replace:{ACCT}:{pos.id}", {"x": 1})
    finally:
        conn.close()
    f = c.get("/api/system/fo").json()["accounts"][0]["flags"]
    assert f["fo_verified"]["FUTURES"] is True and f["fo_halted"] is True
    assert f["fo_day_open"] == TODAY.isoformat() and f["fo_alerts"] == 2
    assert f["fo_ref_iv"] == {str(pos.id): 0.21} and f["fo_pending_entry"] is True
    assert f["fo_replace"] == [pos.id]
    body = c.get("/api/system/fo").text
    assert "00000000" not in body  # 합성 계좌번호(cano) 미노출
    assert _client(project, []).get("/api/system/fo").json()["accounts"] == []
