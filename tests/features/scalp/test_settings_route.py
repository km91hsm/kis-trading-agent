"""scalp `GET·PATCH /settings/scalp` — 계좌별 단타 스위치(실전 켜기는 확인 필요)."""

from __future__ import annotations

from dataclasses import replace

from tests.features.conftest import AUTH
from tests.features.scalp.test_routes import client  # noqa: F401
from trader.kernel.ledger.control import get_flag

URL = "/api/settings/scalp"


def _rows(c):
    return {a["id"]: a for a in c.get(URL).json()["accounts"]}


def _log(c):
    with c.app.state.deps.conn_factory() as conn:
        return conn.execute("SELECT * FROM control_log ORDER BY id").fetchall()


def _patch(c, id_, enabled):
    return c.patch(f"{URL}/{id_}", headers=AUTH, json={"enabled": enabled}).status_code


def test_get_lists_accounts_with_source_and_effective(client):  # noqa: F811
    body = client.get(URL).json()
    assert [a["id"] for a in body["accounts"]] == ["paper-main", "paper-two", "real-1"]
    assert body["accounts"][0]["source"] == "yaml" and body["accounts"][0]["env"] == "paper"
    assert body["accounts"][0]["markets"] == ["KR", "US"]
    real = _rows(client)["real-1"]
    assert real["enabled"] is False and real["source"] == "default"
    assert real["effective"] is False and real["reason"] == "꺼짐"
    cfg = client.app.state.deps.settings.scalp
    assert body["judge"] == cfg.judge and body["max_positions"] == cfg.max_positions
    assert body["max_weight_pct"] == cfg.max_weight_pct and body["default_paper"] == cfg.enabled


def test_patch_requires_token_and_same_origin(client):  # noqa: F811
    assert client.patch(f"{URL}/paper-main", json={"enabled": True}).status_code == 401
    r = client.patch(
        f"{URL}/paper-main", json={"enabled": True},
        headers={"Authorization": "Bearer t", "Origin": "http://evil.example"},
    )  # fmt: skip
    assert r.status_code == 403


def test_patch_real_on_requires_confirm(client):  # noqa: F811
    assert client.patch(f"{URL}/real-1", headers=AUTH, json={"enabled": True}).status_code == 400
    r = client.patch(f"{URL}/real-1", headers=AUTH, json={"enabled": True, "confirm": "real-1"})
    assert r.status_code == 200
    row = {a["id"]: a for a in r.json()["accounts"]}["real-1"]
    assert row["enabled"] is True and row["source"] == "control"
    with client.app.state.deps.conn_factory() as conn:
        flag = get_flag(conn, "scalp_enabled:real-1")
    assert flag["enabled"] is True and flag["confirm"] == "real-1" and "at" in flag
    log = _log(client)
    assert len(log) == 1 and log[0]["key"] == "scalp_enabled:real-1"
    assert log[0]["channel"] == "web"


def test_patch_real_off_no_confirm(client):  # noqa: F811
    client.patch(f"{URL}/real-1", headers=AUTH, json={"enabled": True, "confirm": "real-1"})
    r = client.patch(f"{URL}/real-1", headers=AUTH, json={"enabled": False})
    assert r.status_code == 200
    with client.app.state.deps.conn_factory() as conn:
        assert get_flag(conn, "scalp_enabled:real-1") is False


def test_patch_null_resets_to_default(client):  # noqa: F811
    client.patch(f"{URL}/paper-main", headers=AUTH, json={"enabled": True})
    assert _rows(client)["paper-main"]["source"] == "control"
    r = client.patch(f"{URL}/paper-main", headers=AUTH, json={"enabled": None})
    assert r.status_code == 200
    with client.app.state.deps.conn_factory() as conn:
        assert get_flag(conn, "scalp_enabled:paper-main") is None
    assert _rows(client)["paper-main"]["source"] == "yaml"
    client.patch(f"{URL}/real-1", headers=AUTH, json={"enabled": True, "confirm": "real-1"})
    client.patch(f"{URL}/real-1", headers=AUTH, json={"enabled": None})
    assert _rows(client)["real-1"]["source"] == "default"


def test_patch_unknown_account_404(client):  # noqa: F811
    assert client.patch(f"{URL}/nobody", headers=AUTH, json={"enabled": True}).status_code == 404


def test_patch_rejects_extra_and_non_bool(client):  # noqa: F811
    for bad in ({"enabled": "yes"}, {"enabled": True, "x": 1}):
        assert client.patch(f"{URL}/paper-main", headers=AUTH, json=bad).status_code == 422


def test_patch_logs_only_when_effective_value_changes(client):  # noqa: F811
    deps = client.app.state.deps
    off = deps.settings.scalp.model_copy(update={"enabled": False})
    client.app.state.deps = replace(deps, settings=deps.settings.model_copy(update={"scalp": off}))
    # 모의, yaml False: false 를 쓰면 출처만 바뀌고 값은 그대로 — 이력 없음
    assert _patch(client, "paper-main", False) == 200
    assert _log(client) == []
    # 값이 그대로인 기본값 복귀 — 이력 없음
    assert _patch(client, "paper-main", None) == 200
    assert _log(client) == []
    # 실제 켜기 — 한 줄
    assert _patch(client, "paper-main", True) == 200
    assert len(_log(client)) == 1


def test_patch_real_on_with_other_account_confirm_rejected(client):  # noqa: F811
    body = {"enabled": True, "confirm": "paper-main"}
    assert client.patch(f"{URL}/real-1", headers=AUTH, json=body).status_code == 400
    with client.app.state.deps.conn_factory() as conn:
        assert get_flag(conn, "scalp_enabled:real-1") is None
    assert _log(client) == []
