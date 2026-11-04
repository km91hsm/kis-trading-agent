"""accounts 웹 자원: 계좌 목록·통합 현황·모드 변경·계좌 추가삭제·설정·일시정지·연결 키·SSE 키.

옛 `tests/web/test_accounts_api.py`(4건)·`test_secrets_api.py`(웹 경로 4건 — 부트스트랩 토큰·
Host·IPv6·ensure-fresh 는 커널 `tests/kernel/web`, ensure_web_token·동시 갱신은
`test_secrets_io.py` 가 이미 다룬다)·`test_control_sse.py`(모드·일시정지 3건) 이식 + 신규."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from tests.features.accounts.conftest import make_account
from tests.features.conftest import AUTH
from tests.features.conftest import web_client as make_web_client
from trader.features.accounts import modes, overview
from trader.features.accounts.feature import FEATURE
from trader.features.accounts.ports import AccountsPortImpl
from trader.kernel.config.secrets import Secrets
from trader.kernel.ledger import db
from trader.kernel.web.sse import snapshot

NOW = datetime(2026, 11, 4, 6, 0, tzinfo=UTC)  # KST 15:00, 운영일 KR·US 모두 2026-11-04
LOCAL = ("127.0.0.1", 50000)
LOCAL_AUTH = {"Authorization": "Bearer t", "Origin": "http://127.0.0.1"}

YAML = """\
accounts:
  - {id: paper-main, env: paper, cano: "00000000", markets: [KR, US], mode: auto}
  - {id: real-main, env: real, cano: "00000000", acnt_prdt_cd: "01", markets: [KR], mode: "off"}
"""


@pytest.fixture
def accounts():
    return [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("real-main", "real", "off"),
        make_account("paper-us", "paper", "approve", markets=["US"]),
        make_account("fo-paper", "paper", "auto", asset="fo", acnt_prdt_cd="03"),
    ]


@pytest.fixture
def port(accounts):
    return AccountsPortImpl(accounts, Secrets())


@pytest.fixture
def web_client(project, port):
    return make_web_client(project, [FEATURE], ports={"accounts": port}, now=NOW)


@pytest.fixture
def ledger(project, web_client):
    c = db.connect(project / "state" / "trader.db")
    try:
        yield c
    finally:
        c.close()


def _nav(conn, account_id, d, market, nav, cash, at):
    conn.execute(
        "INSERT INTO account_nav(account_id, date, market, nav, cash, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (account_id, d, market, nav, cash, at),
    )
    conn.commit()


def _pos(conn, account_id, d, market, ticker, qty=1, asset="stock"):
    conn.execute(
        "INSERT INTO positions_snapshot(account_id, date, market, asset, ticker, qty, avg_price, "
        "current_price, eval_amount, created_at) VALUES (?,?,?,?,?,?,'1','1','1','t')",
        (account_id, d, market, asset, ticker, qty),
    )
    conn.commit()


def _pending(conn, account_id):
    conn.execute(
        "INSERT INTO approvals(decision_id, account_id, order_json, status, requested_at, "
        "expires_at, fingerprint) VALUES (0, ?, '{}', 'pending', 't', 't', 'f')",
        (account_id,),
    )
    conn.commit()


@pytest.fixture
def fixtures(ledger):
    """paper-main 은 KR(오늘·전일)·US 스냅샷과 보유 2종목·승인 1건, real-main 은 KR 오늘만,
    paper-us·fo-paper 는 스냅샷 없음."""
    _nav(ledger, "paper-main", "2026-11-03", "KR", "990000", "100000", "2026-11-03T06:30:00+00:00")
    _nav(ledger, "paper-main", "2026-11-04", "KR", "1000000", "120000", "2026-11-04T05:59:00+00:00")
    _nav(ledger, "paper-main", "2026-11-04", "US", "1000.50", "200.25", "2026-11-04T05:58:00+00:00")
    _nav(ledger, "real-main", "2026-11-04", "KR", "500000", "50000", "2026-11-04T05:57:00+00:00")
    _pos(ledger, "paper-main", "2026-11-04", "KR", "005930")
    _pos(ledger, "paper-main", "2026-11-04", "US", "AAPL")
    _pending(ledger, "paper-main")
    return ledger


def _local(client: TestClient) -> TestClient:
    return TestClient(client.app, client=LOCAL, base_url="http://127.0.0.1")


def _rows(client) -> dict[str, dict]:
    r = client.get("/api/accounts")
    assert r.status_code == 200, r.text
    return {a["id"]: a for a in r.json()}


# --- GET /accounts ----------------------------------------------------------------------


def test_accounts_rows_carry_lane_and_pending(web_client, fixtures):
    rows = _rows(web_client)
    assert set(rows) == {"paper-main", "real-main", "paper-us", "fo-paper"}
    assert rows["fo-paper"]["lane"] == "fo" and rows["paper-main"]["lane"] == "spot"
    assert rows["paper-main"]["pending_approvals"] == 1
    assert rows["real-main"]["pending_approvals"] == 0
    assert rows["paper-main"]["positions_count"] == 2
    assert rows["paper-main"]["currency"] == "KRW" and rows["paper-us"]["currency"] == "USD"
    assert rows["paper-main"]["usd_nav"] == "1000.50"
    assert rows["paper-main"]["day_pnl"] == "10000"
    assert rows["real-main"]["day_pnl"] is None  # 전일 스냅샷 없음
    assert rows["paper-main"]["markets"] == ["KR", "US"]
    assert rows["real-main"]["mode"] == "off" and rows["real-main"]["mode_source"] == "yaml"
    assert rows["paper-main"]["paused"] is False
    assert "cano" not in rows["paper-main"]  # 계좌번호는 내보내지 않는다


def test_accounts_rows_carry_cash_and_timestamps(web_client, fixtures):
    rows = _rows(web_client)
    pm = rows["paper-main"]
    assert pm["nav"] == "1000000" and pm["cash"] == "120000"
    assert pm["nav_at"] == "2026-11-04T05:59:00+00:00" == pm["cash_at"]
    us = rows["paper-us"]
    assert us["nav"] is None and us["cash"] is None
    assert us["nav_at"] is None and us["cash_at"] is None


def test_lane_of_rules(project, accounts):
    from tests.features.conftest import load_settings

    s = load_settings(project)
    by = {a.id: a for a in accounts}
    assert overview.lane_of(s, by["fo-paper"]) == "fo"
    assert overview.lane_of(s, by["paper-main"]) == "spot"


def test_accounts_without_port_is_empty(project):
    c = make_web_client(project, [FEATURE], now=NOW)
    assert c.get("/api/accounts").json() == []


# --- GET /accounts/overview -------------------------------------------------------------


@pytest.fixture
def web_client_with_fx(web_client, fixtures, monkeypatch):
    monkeypatch.setattr(
        overview,
        "fx_rate",
        lambda settings, conn, pair="USDKRW": {
            "pair": pair, "rate": "1392.5", "as_of": "2026-11-04T05:00:00+00:00",
        },
    )  # fmt: skip
    return web_client


@pytest.fixture
def web_client_no_fx(web_client, fixtures):
    return web_client  # 스텁 fx_rate 는 None


def test_overview_splits_krw_and_usd_and_reports_fx(web_client_with_fx):
    r = web_client_with_fx.get("/api/accounts/overview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["krw"]["nav"] == "1500000"
    assert body["krw"]["day_pnl"] == "10000"
    assert body["krw"]["accounts"] == ["paper-main", "real-main"]
    assert body["usd"]["nav"] == "1000.50" and body["usd"]["accounts"] == ["paper-main"]
    fx = body["fx"]
    assert fx["pair"] == "USDKRW" and Decimal(fx["rate"]) == Decimal("1392.5")
    expected = (Decimal("1500000") + Decimal("1000.50") * Decimal("1392.5")).quantize(Decimal(1))
    assert fx["converted_total"] == str(expected) == "2893196"
    assert body["paused"] is False and body["at"] == NOW.isoformat()


def test_overview_without_fx_keeps_two_totals(web_client_no_fx):
    r = web_client_no_fx.get("/api/accounts/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["fx"] is None
    assert body["krw"]["nav"] == "1500000" and body["usd"]["nav"] == "1000.50"


def test_overview_excludes_fo_accounts(web_client, fixtures):
    _nav(fixtures, "fo-paper", "2026-11-04", "KR", "7", "7", "2026-11-04T05:00:00+00:00")
    body = web_client.get("/api/accounts/overview").json()
    assert "fo-paper" not in body["krw"]["accounts"] and body["krw"]["nav"] == "1500000"


# --- PATCH /accounts/{id} (옛 test_control_sse 모드 2건) ------------------------------------


def test_web_mode_real_auto_requires_confirm(web_client, ledger, accounts):
    url = "/api/accounts/real-main"
    r = web_client.patch(url, json={"mode": "auto"}, headers=AUTH)
    assert r.status_code == 400 and "confirm" in r.json()["detail"]
    assert _rows(web_client)["real-main"]["mode"] == "off"
    r = web_client.patch(url, json={"mode": "auto", "confirm": "real-main"}, headers=AUTH)
    assert r.status_code == 409 and "LIVE 확인 필요" in r.json()["detail"]
    assert _rows(web_client)["real-main"]["mode"] == "off"
    # LIVE 확인은 사용자 CLI(`accounts mode … --confirm LIVE`)가 남긴다 — scratch 원장에서 흉내
    r = web_client.patch(url, json={"mode": "auto", "confirm": "paper-main"}, headers=AUTH)
    assert r.status_code == 400  # 다른 계좌 id 는 확인이 아니다
    modes.set_account_mode(ledger, accounts[1], "auto", confirm=modes.LIVE_CONFIRM)
    ledger.execute(  # 사용자가 예전에 남긴 확인(시각이 지금과 달라야 덮어쓰기가 드러난다)
        "UPDATE account_modes SET live_confirmed_json='{\"confirm\": \"LIVE\", "
        "\"at\": \"2026-01-01T00:00:00+00:00\"}', updated_at='2026-01-01T00:00:00+00:00' "
        "WHERE account_id='real-main'"
    )
    ledger.commit()
    sql = "SELECT live_confirmed_json, updated_at FROM account_modes WHERE account_id='real-main'"
    before = tuple(ledger.execute(sql).fetchone())
    n_log = ledger.execute("SELECT COUNT(*) FROM control_log").fetchone()[0]
    r = web_client.patch(url, json={"mode": "auto", "confirm": "real-main"}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"account_id": "real-main", "mode": "auto"}
    # 웹은 LIVE 확인을 다시 쓰지 않는다(T6-R1): 행·변경 이력 그대로
    assert tuple(ledger.execute(sql).fetchone()) == before
    assert ledger.execute("SELECT COUNT(*) FROM control_log").fetchone()[0] == n_log
    assert web_client.patch(
        "/api/accounts/ghost", json={"mode": "off"}, headers=AUTH
    ).status_code == 404  # fmt: skip
    assert web_client.patch(
        "/api/accounts/paper-main", json={"mode": "fast"}, headers=AUTH
    ).status_code == 422  # fmt: skip


def test_web_mode_paper_approve(web_client, ledger):
    r = web_client.patch("/api/accounts/paper-main", json={"mode": "approve"}, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"account_id": "paper-main", "mode": "approve"}
    row = _rows(web_client)["paper-main"]
    assert row["mode"] == "approve" and row["mode_source"] == "control"
    log = ledger.execute("SELECT * FROM control_log ORDER BY id DESC LIMIT 1").fetchone()
    assert log["key"] == "account_mode:paper-main" and log["channel"] == "web"
    assert log["actor"]


def test_patch_mode_requires_token_and_same_origin(web_client):
    assert web_client.patch("/api/accounts/paper-main", json={"mode": "off"}).status_code == 401
    r = web_client.patch(
        "/api/accounts/paper-main",
        json={"mode": "off"},
        headers={**AUTH, "Origin": "http://evil.example"},
    )
    assert r.status_code == 403


def test_real_mode_off_needs_confirm_too(web_client):
    r = web_client.patch("/api/accounts/real-main", json={"mode": "approve"}, headers=AUTH)
    assert r.status_code == 400
    r = web_client.patch(
        "/api/accounts/real-main", json={"mode": "approve", "confirm": "real-main"}, headers=AUTH
    )
    assert r.status_code == 200


# --- POST /accounts · DELETE /accounts/{id} (옛 test_accounts_api 4건) ------------------


def _yml(project):
    p = project / "config" / "accounts.yaml"
    p.write_text(YAML, encoding="utf-8")
    return p


def test_add_remove_require_token_and_direct_local(project, web_client):
    _yml(project)
    c = _local(web_client)
    body = {"id": "real-2", "env": "real", "markets": ["KR"]}
    assert c.post("/api/accounts", json=body).status_code == 401
    assert c.delete("/api/accounts/x").status_code == 401
    proxied = c.post(
        "/api/accounts",
        headers={**LOCAL_AUTH, "Tailscale-User-Login": "me@example.com",
                 "X-Forwarded-For": "100.1.2.3"},
        json=body,
    )  # fmt: skip
    assert proxied.status_code == 403
    remote = TestClient(web_client.app, client=("100.64.0.9", 1), base_url="http://127.0.0.1")
    assert remote.post("/api/accounts", headers=LOCAL_AUTH, json=body).status_code == 403
    assert remote.delete("/api/accounts/real-main", headers=LOCAL_AUTH).status_code == 403


def test_add_and_remove(project, web_client):
    p = _yml(project)
    c = _local(web_client)
    r = c.post(
        "/api/accounts",
        headers=LOCAL_AUTH,
        json={"id": "real-2", "env": "real", "asset": "stock", "markets": ["KR", "US"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["restart_required"] is True and r.json()["id"] == "real-2"
    assert "real-2" in p.read_text(encoding="utf-8")

    r = c.delete("/api/accounts/real-2", headers=LOCAL_AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["restart_required"] is True
    assert "real-2" not in p.read_text(encoding="utf-8")


def test_add_rejects_bad_input(project, web_client):
    _yml(project)
    c = _local(web_client)
    dup = c.post("/api/accounts", headers=LOCAL_AUTH, json={"id": "paper-main", "env": "paper"})
    assert dup.status_code == 400 and "이미" in dup.json()["detail"]
    bad = c.post("/api/accounts", headers=LOCAL_AUTH, json={"id": "a b", "env": "paper"})
    assert bad.status_code == 400
    extra = c.post(
        "/api/accounts", headers=LOCAL_AUTH, json={"id": "p2", "env": "paper", "cano": "1"}
    )
    assert extra.status_code == 422


def test_remove_blocked_by_live_order_is_409(project, web_client, ledger):
    _yml(project)
    ledger.execute(
        "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, status, mode,"
        " created_at) VALUES ('real-main','005930','KR','BUY',1,'limit','accepted','off','t')"
    )
    ledger.commit()
    r = _local(web_client).delete("/api/accounts/real-main", headers=LOCAL_AUTH)
    assert r.status_code == 409 and "주문" in r.json()["detail"]


# --- GET /settings ----------------------------------------------------------------------


def test_settings_shape_counts_tailnet_without_names(web_client):
    r = web_client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["markets"] == ["KR", "US"]
    assert {"risk", "debate_rounds", "models", "llm"} <= set(body)
    web = body["web"]
    assert web["tailscale_hosts_count"] == 0 and web["tailscale_users_count"] == 0
    assert "tailscale_users" not in web and "tailscale_hosts" not in web
    assert {"budget_usd", "daily_session_cap"} <= set(body["llm"])


# --- PATCH /settings/paused (옛 test_pause_resume_flags + 신규) ----------------------------


def test_pause_resume_flags(web_client, ledger):
    r = web_client.patch("/api/settings/paused", json={"value": True}, headers=AUTH)
    assert r.status_code == 200 and r.json()["paused"] is True
    r = web_client.patch("/api/settings/paused", json={"value": False}, headers=AUTH)
    assert r.status_code == 200 and r.json()["paused"] is False
    log = ledger.execute("SELECT * FROM control_log WHERE key='paused' ORDER BY id").fetchall()
    assert [r["channel"] for r in log] == ["web", "web"]
    assert web_client.patch("/api/settings/paused", json={"value": True}).status_code == 401


@pytest.fixture
def real_auto_account(ledger, accounts):
    modes.set_account_mode(ledger, accounts[1], "auto", confirm=modes.LIVE_CONFIRM)
    return accounts[1]


def test_patch_paused_resume_requires_confirm_when_real_auto(web_client, real_auto_account):
    url = "/api/settings/paused"
    assert web_client.patch(url, json={"value": True}, headers=AUTH).status_code == 200
    r = web_client.patch(url, json={"value": False}, headers=AUTH)
    assert r.status_code == 400
    r = web_client.patch(url, json={"value": False, "confirm": "paper-main"}, headers=AUTH)
    assert r.status_code == 400
    r = web_client.patch(url, json={"value": False, "confirm": "real-main"}, headers=AUTH)
    assert r.status_code == 200 and r.json()["paused"] is False


# --- /settings/secrets (옛 test_secrets_api 웹 경로) ---------------------------------------


@pytest.fixture
def secrets_accounts():
    return [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("real-main", "real", "off"),
    ]


@pytest.fixture
def secrets_client(project, secrets_accounts):
    port = AccountsPortImpl(secrets_accounts, Secrets())
    return _local(make_web_client(project, [FEATURE], ports={"accounts": port}, now=NOW))


def _env(project):
    return project / "secrets" / ".env"


def test_status_shows_set_flags_without_values(project, secrets_client):
    _env(project).write_text("KIS_REAL_APP_KEY=topsecret\n# 메모\n", encoding="utf-8")
    r = secrets_client.get("/api/settings/secrets", headers=LOCAL_AUTH)
    assert r.status_code == 200
    assert "topsecret" not in r.text
    keys = {k["name"]: k for k in r.json()["keys"]}
    assert keys["KIS_REAL_APP_KEY"]["set"] is True and keys["KIS_HTS_ID"]["set"] is False
    assert keys["KIS_REAL_MAIN_APP_KEY"]["account"] == "real-main"
    uses = {a["id"]: a["uses"] for a in r.json()["accounts"]}
    assert uses == {"paper-main": "없음", "real-main": "공용"}


def test_update_writes_backup_and_clears(project, secrets_client):
    _env(project).write_text("# 메모\nKIS_HTS_ID=old\nTELEGRAM_CHAT_ID=1\n", encoding="utf-8")
    r = secrets_client.post(
        "/api/settings/secrets",
        headers=LOCAL_AUTH,
        json={
            "values": {"KIS_HTS_ID": "newid", "KIS_REAL_MAIN_APP_KEY": "k"},
            "clear": ["TELEGRAM_CHAT_ID"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == ["KIS_HTS_ID", "KIS_REAL_MAIN_APP_KEY"] and body["restart_required"]
    assert "newid" not in r.text
    text = _env(project).read_text(encoding="utf-8")
    assert "# 메모" in text and "KIS_HTS_ID=newid" in text and "TELEGRAM_CHAT_ID" not in text
    assert (project / "secrets" / body["backup"]).read_text(encoding="utf-8").count("old") == 1


def test_update_rejects_unknown_names_and_multiline(project, secrets_client):
    c, url = secrets_client, "/api/settings/secrets"
    assert c.post(url, headers=LOCAL_AUTH, json={"values": {"PATH": "/x"}}).status_code == 400
    for v in ("a\nWEB_TOKEN=x", "a b", "${WEB_TOKEN}"):
        r = c.post(url, headers=LOCAL_AUTH, json={"values": {"KIS_HTS_ID": v}})
        assert r.status_code == 400
    assert not _env(project).exists()


def test_secrets_require_token_and_direct_local(project, secrets_client):
    c, url = secrets_client, "/api/settings/secrets"
    assert c.get(url).status_code == 401
    assert c.post(url, json={"values": {}}).status_code == 401
    proxied = c.get(
        url,
        headers={**LOCAL_AUTH, "Tailscale-User-Login": "me@example.com",
                 "X-Forwarded-For": "100.1.2.3"},
    )  # fmt: skip
    assert proxied.status_code == 403
    remote = TestClient(secrets_client.app, client=("100.64.0.9", 1), base_url="http://127.0.0.1")
    assert remote.get(url, headers=LOCAL_AUTH).status_code == 403


def test_status_reflects_env_overrides(project, secrets_client, monkeypatch):
    monkeypatch.setenv("KIS_REAL_MAIN_APP_KEY", "from-env")
    monkeypatch.setenv("KIS_REAL_MAIN_APP_SECRET", "from-env-s")
    r = secrets_client.get("/api/settings/secrets", headers=LOCAL_AUTH).json()
    keys = {k["name"]: k for k in r["keys"]}
    assert keys["KIS_REAL_MAIN_APP_KEY"]["set"] and keys["KIS_REAL_MAIN_APP_KEY"]["env"]
    assert not keys["KIS_HTS_ID"]["env"]
    assert {a["id"]: a["uses"] for a in r["accounts"]}["real-main"] == "전용"
    w = secrets_client.post(
        "/api/settings/secrets",
        headers=LOCAL_AUTH,
        json={"values": {"KIS_REAL_MAIN_APP_KEY": "x"}},
    )
    assert w.json()["env_overridden"] == ["KIS_REAL_MAIN_APP_KEY"]


# --- SSE 키 · nav ------------------------------------------------------------------------


def test_feature_declares_sse_keys_and_nav():
    assert [k.key for k in FEATURE.sse_keys] == ["accounts", "positions", "approvals"]
    assert all(not k.noisy for k in FEATURE.sse_keys)
    assert [(n.screen, n.id, n.label, n.order) for n in FEATURE.nav] == [
        ("accounts", "overview", "통합", 0)
    ]


def test_sse_probes_change_with_modes_positions_and_approvals(web_client, ledger, accounts):
    deps = web_client.app.state.deps
    first = snapshot(deps)
    assert {"accounts", "positions", "approvals"} <= set(first)
    modes.set_account_mode(ledger, accounts[0], "approve")
    second = snapshot(deps)
    assert second["accounts"] != first["accounts"]
    modes.set_paused(ledger, True)
    third = snapshot(deps)
    assert third["accounts"] != second["accounts"]
    _nav(ledger, "paper-main", "2026-11-04", "KR", "1", "1", "2026-11-04T05:00:00+00:00")
    fourth = snapshot(deps)
    assert fourth["positions"] != third["positions"]
    assert fourth["approvals"] == third["approvals"]
    _pending(ledger, "paper-main")
    fifth = snapshot(deps)
    assert fifth["approvals"] != fourth["approvals"]
    ledger.execute("UPDATE approvals SET status='rejected'")
    ledger.commit()
    assert snapshot(deps)["approvals"] != fifth["approvals"]
