"""saveticker 「시스템」 웹: `GET /system/saveticker`(옛 `system.saveticker_status`)·
`POST /system/saveticker/restart`(토큰 + 같은 출처). 재시작은 control 플래그
`saveticker_restart_requested` 만 세운다 — 웹은 수집 프로세스·파일을 건드리지 않고 데몬 잡이
소비한다(T11-R2, 소비 쪽은 `test_job.py`). 브라우저·네트워크 없음."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.features.conftest import AUTH, web_client
from trader.features.saveticker import automation
from trader.features.saveticker.feature import FEATURE
from trader.features.saveticker.paths import home
from trader.features.saveticker.store import atomic_json
from trader.kernel.ledger import control, db
from trader.kernel.registry.types import NavItem

NOW = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)
FLAG = "saveticker_restart_requested"


@pytest.fixture
def client(project):
    return web_client(project, [FEATURE], now=NOW)


def _ledger(project):
    return db.connect(project / "state" / "trader.db")


def test_saveticker_status_and_restart_api(project, client):
    """상태 조회(토큰 없음)·다시 시작 요청(토큰). 요청은 플래그만 — 수집기 파일은 그대로."""
    assert client.get("/api/system/saveticker").json()["configured"] is False
    atomic_json(home() / "runtime/bridge-config.json", {"backend": "extension"})
    r = client.get("/api/system/saveticker").json()
    assert r["configured"] is True and r["service"]["state"] == "waiting_chrome"
    assert "backend" not in r
    assert client.post("/api/system/saveticker/restart").status_code == 401
    bad_origin = {**AUTH, "Origin": "http://evil.example"}
    assert client.post("/api/system/saveticker/restart", headers=bad_origin).status_code == 403
    before = sorted(p.relative_to(home()) for p in home().rglob("*"))
    r = client.post("/api/system/saveticker/restart", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["requested"]["at"] == NOW.isoformat()
    # 재개는 Chrome 확장 팝업에서 — 화면에도 안내한다
    assert "Chrome" in body["note"]
    # 웹은 수집기 파일을 바꾸지 않는다 — 데몬 잡이 플래그를 소비한다
    assert sorted(p.relative_to(home()) for p in home().rglob("*")) == before
    assert automation.registered(home())
    conn = _ledger(project)
    try:
        flag = control.get_flag(conn, FLAG)
        assert flag["at"] == NOW.isoformat() and flag["actor"]
        log = conn.execute("SELECT channel, key, new FROM control_log").fetchall()
    finally:
        conn.close()
    assert [(x["channel"], x["key"]) for x in log] == [("web", FLAG)]


def test_feature_declares_restart_namespace_and_nav():
    assert FLAG in FEATURE.control_namespaces
    assert NavItem("system", "saveticker", "데이터 수집", 40) in FEATURE.nav
