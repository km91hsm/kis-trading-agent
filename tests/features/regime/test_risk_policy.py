"""시장별 현물 정책의 인증·원자성·운영일 회귀."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.features.conftest import AUTH, web_client
from trader.features.regime.feature import FEATURE
from trader.kernel.ledger import control

NOW = datetime(2026, 11, 10, 1, tzinfo=UTC)


def _client(project, accounts=()):
    return web_client(project, [FEATURE], now=NOW, ports={
        "accounts": SimpleNamespace(accounts=lambda: list(accounts)),
    })


def _account(id, env="real", markets=("KR",), asset="stock"):
    return SimpleNamespace(id=id, env=env, markets=markets, asset=asset)


def test_policy_contract_missing_data_is_unknown(project):
    r = _client(project).get("/api/settings/risk-policy")
    assert r.status_code == 200
    kr = r.json()["markets"][0]
    assert kr["regime"] is None and kr["ai_view"] is None
    assert kr["policy"]["mode"] == "AUTO"
    assert kr["effective"]["entry"] == "unknown"


def test_multiple_real_confirmation_is_atomic_and_market_scoped(project, conn):
    c = _client(project, [_account("one"), _account("two"), _account("us", markets=("US",)),
                          _account("fo", asset="fo")])
    body = {"mode": "RISK_ON", "reason": "수동 검토", "confirmations": {"one": "one"}}
    assert c.patch("/api/settings/risk-policy/KR", json=body, headers=AUTH).status_code == 400
    assert conn.execute("SELECT COUNT(*) FROM control_log").fetchone()[0] == 0
    assert control.get_flag(conn, "risk_policy:KR") is None
    body["confirmations"]["two"] = "two"
    r = c.patch("/api/settings/risk-policy/KR", json=body, headers=AUTH)
    assert r.status_code == 200
    by = {m["market"]: m for m in r.json()["markets"]}
    assert by["KR"]["policy"]["mode"] == "RISK_ON"
    assert by["KR"]["effective"]["max_position_pct"] == 0.1
    assert by["KR"]["effective"]["max_positions"] == 2
    assert by["US"]["policy"]["mode"] == "AUTO"
    assert by["KR"]["history"][0]["reason"] == "수동 검토"
    assert by["KR"]["accounts"] == [{"id": "one", "env": "real"}, {"id": "two", "env": "real"}]


@pytest.mark.parametrize("body", [{"mode": "RISK_ON", "reason": "  "},
                                  {"mode": "bad", "reason": "사유"}])
def test_rejects_invalid_policy(project, body):
    c = _client(project)
    assert c.patch("/api/settings/risk-policy/KR", json=body, headers=AUTH).status_code == 422


def test_write_requires_auth_origin_and_valid_market(project):
    c = _client(project)
    body = {"mode": "RISK_OFF", "reason": "검토"}
    assert c.patch("/api/settings/risk-policy/KR", json=body).status_code == 401
    assert c.patch("/api/settings/risk-policy/KR", json=body,
                   headers={"Authorization": "Bearer t"}).status_code == 403
    assert c.patch("/api/settings/risk-policy/XX", json=body, headers=AUTH).status_code == 400


def test_us_midnight_preserves_policy_ai_and_expiry_is_read_only(project, conn):
    c = _client(project)
    now = datetime(2026, 11, 10, 15, 30, tzinfo=UTC)  # 11일 00:30 KST = US 10일
    c.app.state.deps = replace(c.app.state.deps, now=lambda: now)
    ai = {"date": "2026-11-10", "stance": "RISK_OFF", "confidence": 0.31,
          "rationale": "원본 판단"}
    control.set_flag(conn, "market_view:US", ai)
    r = c.patch("/api/settings/risk-policy/US", json={"mode": "RISK_ON", "reason": "검토"},
                headers=AUTH)
    us = next(m for m in r.json()["markets"] if m["market"] == "US")
    assert us["policy"]["date"] == "2026-11-10" and us["ai_view"] == ai
    saved = control.get_flag(conn, "risk_policy:US")
    c.app.state.deps = replace(c.app.state.deps, now=lambda: now + timedelta(days=1))
    rows = c.get("/api/settings/risk-policy").json()["markets"]
    us = next(m for m in rows if m["market"] == "US")
    assert us["policy"]["mode"] == "AUTO"
    assert control.get_flag(conn, "risk_policy:US") == saved
    assert control.get_flag(conn, "market_view:US") == ai


def test_malformed_saved_manual_policy_does_not_enable_buy(project, conn):
    c = _client(project)
    control.set_flag(conn, "risk_policy:KR", {"mode": "RISK_ON", "date": "2026-11-10",
                                            "expires_at": "broken", "reason": "검토"})
    kr = c.get("/api/settings/risk-policy").json()["markets"][0]
    assert kr["policy"]["mode"] == "AUTO"
    assert kr["effective"]["entry"] == "unknown"


def test_policy_and_history_rollback_together(project, conn):
    from trader.features.regime import policy
    conn.execute("CREATE TRIGGER reject_policy_log BEFORE INSERT ON control_log "
                 "BEGIN SELECT RAISE(ABORT, '합성 기록 실패'); END")
    conn.commit()
    with pytest.raises(Exception, match="합성 기록 실패"):
        policy.save(conn, "KR", "RISK_ON", "검토", now=NOW, actor="test")
    assert control.get_flag(conn, "risk_policy:KR") is None
