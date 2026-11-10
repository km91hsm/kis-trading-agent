"""파생 계좌별 스위치(단타 `scalp_enabled:<계좌>` 와 같은 모양): control `fo_enabled:<계좌 id>`.

저장값이 없으면 모의는 yaml `fo.enabled`, 실전은 꺼짐(실전 파생은 항상 off — 켤 수 없다).
게이트(신규 진입)·회수 사유·아침 세션·웹 상태가 계좌별 값을 본다. 모의 계좌는 현물처럼 검증
플래그 없이 바로 매매한다(`executor.is_verified`)."""

from __future__ import annotations

from tests.features.conftest import AUTH
from tests.features.fo import test_session as ts
from tests.features.fo._support import account, make_ctx, settings
from tests.features.fo.test_routes import _client, _fo_account
from trader.features.fo import executor, switch
from trader.features.fo.executor import VEHICLES, verified_key
from trader.features.fo.feature import FEATURE
from trader.kernel.broker.fo import CONTRACT_VERSION
from trader.kernel.ledger import control


def test_stored_defaults_and_control(conn):
    paper, real = account(), account(env="real")
    assert switch.stored(conn, settings(enabled=False), paper) == (False, "yaml")
    assert switch.stored(conn, settings(enabled=True), paper) == (True, "yaml")
    assert switch.stored(conn, settings(enabled=True), real) == (False, "default")
    control.set_flag(conn, switch.flag_key(paper.id), True)
    assert switch.stored(conn, settings(enabled=False), paper) == (True, "control")
    control.set_flag(conn, switch.flag_key(paper.id), "yes")  # 비불리언은 켜지지 않는다
    assert switch.stored(conn, settings(enabled=False), paper)[0] is False
    control.set_flag(conn, switch.flag_key(real.id), True)  # 실전은 저장값이 있어도 꺼짐
    assert switch.stored(conn, settings(enabled=True), real)[0] is False


def test_feature_declares_namespace():
    assert "fo_enabled" in FEATURE.control_namespaces


def test_portfolio_carries_account_switch_and_gate_uses_it(conn):
    ctx = make_ctx(conn, enabled=False)
    a = ctx.accounts[0]
    assert executor.build_portfolio(ctx, a, None, ctx.now().date()).enabled is False
    control.set_flag(conn, switch.flag_key(a.id), True)
    assert executor.build_portfolio(ctx, a, None, ctx.now().date()).enabled is True


def test_session_runs_when_account_switched_on(project, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    assert "꺼짐" in (ts.fo_session.run(**ts._deps(conn, enabled=False))["skipped"] or "")
    control.set_flag(conn, switch.flag_key(account().id), True)
    out = ts.fo_session.run(**ts._deps(conn, enabled=False))
    assert out["skipped"] is None and out["session"]["ok"]


def test_paper_is_verified_without_flag_real_needs_it(conn):
    s = settings()
    assert all(executor.is_verified(conn, s, account(), v) for v in VEHICLES)
    real = account(env="real")
    assert not executor.is_verified(conn, s, real, "FUTURES")
    control.set_flag(conn, verified_key(s, real, "FUTURES"), CONTRACT_VERSION)
    assert executor.is_verified(conn, s, real, "FUTURES")


def test_portfolio_paper_vehicles_verified_without_flags(conn):
    ctx = make_ctx(conn)
    for v in VEHICLES:  # make_ctx 가 심어 둔 플래그를 지운다
        control.delete_flag(conn, verified_key(ctx.settings, ctx.accounts[0], v))
    port = executor.build_portfolio(ctx, ctx.accounts[0], None, ctx.now().date())
    assert port.verified == {v: True for v in VEHICLES}


def test_settings_api_switch_per_account(project):
    c = _client(project, [_fo_account(), _fo_account(env="real")], enabled=False)
    got = c.get("/api/settings/fo").json()
    rows = {a["id"]: a for a in got["accounts"]}
    assert rows["paper-fo"]["enabled"] is False and rows["paper-fo"]["source"] == "yaml"
    assert rows["real-fo"]["enabled"] is False and rows["real-fo"]["source"] == "default"
    url = "/api/settings/fo/paper-fo"
    assert c.patch(url, json={"enabled": True}).status_code == 401
    assert c.patch(url, json={"enabled": "x"}, headers=AUTH).status_code == 422
    r = c.patch(url, json={"enabled": True}, headers=AUTH)
    assert r.status_code == 200
    row = {a["id"]: a for a in r.json()["accounts"]}["paper-fo"]
    assert row["enabled"] is True and row["source"] == "control" and row["effective"] is True
    st = c.get("/api/system/fo").json()["accounts"]
    assert {a["id"]: a for a in st}["paper-fo"]["state"] == "active"
    with c.app.state.deps.conn_factory() as conn:
        log = conn.execute("SELECT channel, key, new FROM control_log ORDER BY id DESC").fetchone()
    assert (log["channel"], log["key"], log["new"]) == ("web", "fo_enabled:paper-fo", "true")
    on = {"enabled": True}
    assert c.patch("/api/settings/fo/real-fo", json=on, headers=AUTH).status_code == 400
    assert c.patch("/api/settings/fo/nobody", json=on, headers=AUTH).status_code == 404
    r = c.patch(url, json={"enabled": None}, headers=AUTH)
    assert {a["id"]: a for a in r.json()["accounts"]}["paper-fo"]["source"] == "yaml"


def test_session_limits_enabled_follows_account_switch(conn):
    """세션 입력 한도 요약의 `enabled` 도 계좌 스위치를 따른다(yaml 꺼짐이어도 켠 계좌면 참)."""
    from trader.features.fo import inputs

    ctx = make_ctx(conn, enabled=False)
    assert inputs._limits(ctx, None)["enabled"] is False
    control.set_flag(conn, switch.flag_key(ctx.accounts[0].id), True)
    assert inputs._limits(ctx, None)["enabled"] is True
