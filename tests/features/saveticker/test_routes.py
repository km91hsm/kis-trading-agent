"""saveticker 「시스템」 웹: `GET /system/saveticker`(옛 `system.saveticker_status`)·
`POST /system/saveticker/restart`(토큰 + 같은 출처). 재시작은 control 플래그
`saveticker_restart_requested` 만 세운다 — 웹은 수집 프로세스·파일을 건드리지 않고 데몬 잡이
소비한다(T11-R2, 소비 쪽은 `test_job.py`). 브라우저·네트워크 없음."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

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


# --- 웹 설치·첫 안내 (2026-11-08) -------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from trader.features.saveticker import bridge_install  # noqa: E402

LOCAL_AUTH = {"Authorization": "Bearer t", "Origin": "http://127.0.0.1"}
ONBOARDING = "saveticker.onboarding_dismissed"


def _local(client):
    return TestClient(client.app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1")


@pytest.fixture
def fake_install(monkeypatch, tmp_path):
    calls = []
    pkg = tmp_path / "pkg"

    def status(**_):
        return {
            "registered": bool(calls),
            "registration": "/r.json",
            "host_path": "/h",
            "package": str(pkg),
            "installed_version": "1.5.0" if calls else None,
            "bundled_version": "1.5.0",
            "update_needed": False,
        }

    def install(**_):
        calls.append(1)
        return {
            "ok": True,
            "package": str(pkg),
            "extension_dir": str(pkg / "extension"),
            "extension_version": "1.5.0",
            "registration": "/r.json",
            "host_path": "/h",
            "replaced": False,
            "backend": "extension",
            "next_steps": ["a"],
        }

    monkeypatch.setattr(bridge_install, "install_status", status)
    monkeypatch.setattr(bridge_install, "install", install)
    return calls


def test_status_includes_install_and_onboarding(client, fake_install):
    r = _local(client).get("/api/system/saveticker").json()
    assert r["install"]["registered"] is False
    assert r["install"]["extension_dir"].replace("\\", "/").endswith("/pkg/extension")
    assert r["onboarding"] == {"dismissed": False}
    remote = client.get("/api/system/saveticker").json()["install"]
    assert remote["package"] is None and remote["extension_dir"] is None
    assert "registration" not in remote and "host_path" not in remote


def test_install_requires_local_token_origin(client, fake_install):
    local = _local(client)
    assert local.post("/api/system/saveticker/install").status_code == 401
    bad = {**LOCAL_AUTH, "Origin": "http://evil.example"}
    assert local.post("/api/system/saveticker/install", headers=bad).status_code == 403
    proxied = {**LOCAL_AUTH, "X-Forwarded-For": "10.0.0.2"}
    assert local.post("/api/system/saveticker/install", headers=proxied).status_code == 403
    assert client.post("/api/system/saveticker/install", headers=AUTH).status_code == 403
    assert fake_install == []
    r = local.post("/api/system/saveticker/install", headers=LOCAL_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["next_steps"] == ["a"] and body["replaced"] is False
    assert body["extension"].replace("\\", "/").endswith("/pkg/extension")
    assert fake_install == [1]


def test_install_error_is_korean_400(client, monkeypatch):
    def boom(**_):
        raise bridge_install.InstallError("registration_exists")

    monkeypatch.setattr(bridge_install, "install", boom)
    r = _local(client).post("/api/system/saveticker/install", headers=LOCAL_AUTH)
    assert r.status_code == 400
    assert "다른 프로그램" in r.json()["detail"] and "registration_exists" not in r.json()["detail"]


def test_onboarding_dismiss_persists_and_logs(project, client, fake_install):
    assert (
        client.post("/api/system/saveticker/onboarding", json={"dismissed": True}).status_code
        == 401
    )
    r = client.post("/api/system/saveticker/onboarding", headers=AUTH, json={"dismissed": True})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert client.get("/api/system/saveticker").json()["onboarding"] == {"dismissed": True}
    conn = _ledger(project)
    try:
        keys = [x["key"] for x in conn.execute("SELECT key FROM control_log").fetchall()]
    finally:
        conn.close()
    assert keys == [ONBOARDING]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 는 설치판에서만 설치(소스 설치 거부)")
def test_install_route_returns_real_extension_dir(project, client, tmp_path, monkeypatch):
    """계약: 가짜가 아닌 실제 `install()` 반환값으로 경로가 채워진다(키 이름 불일치 회귀 방지)."""
    real = bridge_install.install
    monkeypatch.setattr(bridge_install, "install", lambda **_: real(user_home=tmp_path / "user"))
    r = _local(client).post("/api/system/saveticker/install", headers=LOCAL_AUTH)
    assert r.status_code == 200, r.text
    ext = r.json()["extension"]
    assert ext != "None" and (Path(ext) / "manifest.json").is_file()


def test_concurrent_install_is_refused_in_korean(project, client, tmp_path):
    with bridge_install._INSTALL_LOCK:
        with pytest.raises(bridge_install.InstallError, match="install_busy"):
            bridge_install.install(user_home=tmp_path / "user")
        assert bridge_install.sync(user_home=tmp_path / "user") == {"skipped": "busy"}
        r = _local(client).post("/api/system/saveticker/install", headers=LOCAL_AUTH)
    assert r.status_code == 400 and "진행 중" in r.json()["detail"]


def test_install_os_error_is_korean_400(client, monkeypatch):
    def boom(**_):
        raise PermissionError("denied")

    monkeypatch.setattr(bridge_install, "install", boom)
    r = _local(client).post("/api/system/saveticker/install", headers=LOCAL_AUTH)
    assert r.status_code == 400 and "파일" in r.json()["detail"]


def test_daemon_sync_retries_next_tick_when_install_busy(monkeypatch):
    from trader.features.saveticker import job

    results = [{"skipped": "busy"}, {"extension_updated": False, "host_updated": False}]
    calls = []
    monkeypatch.setattr(job._STATE, "synced", False)
    monkeypatch.setattr(bridge_install, "sync", lambda: calls.append(1) or results[len(calls) - 1])
    assert job.sync_extension(None) is None
    assert job.sync_extension(None) is None
    assert calls == [1, 1] and job._STATE.synced is True
    assert job.sync_extension(None) is None and calls == [1, 1]
