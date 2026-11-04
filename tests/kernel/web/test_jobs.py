"""잡 자원 `/api/jobs`: 토큰·데몬 생존·검증·중복·취소·실전 confirm (옛 test_jobs_api 이식)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tests.kernel.web.conftest import AUTH, NOW, ledger_deps, make_client
from trader.kernel.daemon import queue as jobq
from trader.kernel.registry import Feature
from trader.kernel.registry.types import JobSpec, QueueKind
from trader.kernel.web.routers import jobs as jobs_router


class _Market(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: Literal["KR", "US"]


class _Account(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str


class _Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _qk(kind, model, label, lane, trading=False):
    return QueueKind(kind, model, label, lane, 0, trading, False, lambda d, p, g: {})


def _feature() -> Feature:
    job = JobSpec(
        id="{market}_fx_scan", kind="fx_scan", trigger="cron", per_market=True,
        schedule_key="{market}.fx_scan", interval_s=None, enabled=None, gate="none",
        heavy=False, misfire="default", catch_up="none", start_soon_s=0, body=lambda *a: {},
    )  # fmt: skip
    return Feature(
        id="fx",
        tier="service",
        jobs=(job,),
        queue_kinds=(
            _qk("fx_scan", _Market, "시장 스캔", "slow"),
            _qk("fx_order", _Account, "계좌 작업", "fast", trading=True),
            _qk("fx_info", _Empty, "정보 확인", "info"),
            _qk("fx_all", _Empty, "전 계좌 작업", "fast", trading=True),
        ),
    )


class _Accounts:
    def accounts(self):
        return [
            SimpleNamespace(id="paper-1", env="paper"),
            SimpleNamespace(id="real-1", env="real"),
        ]


def _client(project, *, ports=None):
    deps = ledger_deps(project, features=[_feature()], ports=ports or {"accounts": _Accounts()})
    return make_client(deps), deps


def _alive(monkeypatch, value: bool):
    monkeypatch.setattr(jobs_router, "daemon_alive", lambda deps: value)


def test_post_job_requires_token_and_running_daemon(project, monkeypatch):
    client, _ = _client(project)
    assert client.post("/api/jobs", json={"kind": "fx_info"}).status_code == 401
    _alive(monkeypatch, False)
    r = client.post("/api/jobs", json={"kind": "fx_info"}, headers=AUTH)
    assert r.status_code == 409 and "데몬이 꺼져" in r.json()["detail"]
    assert client.get("/api/jobs/requests").json() == {"alive": False, "active": 0, "requests": []}


def test_post_job_requires_same_origin(project, monkeypatch):
    client, _ = _client(project)
    _alive(monkeypatch, True)
    token_only = {"Authorization": "Bearer secret"}
    r = client.post("/api/jobs", json={"kind": "fx_info"}, headers=token_only)
    assert r.status_code == 403


def test_post_job_validates_kind_and_params(project, monkeypatch):
    client, _ = _client(project)
    _alive(monkeypatch, True)
    r = client.post("/api/jobs", json={"kind": "nope"}, headers=AUTH)
    assert r.status_code == 400 and "알 수 없는 작업" in r.json()["detail"]
    r = client.post("/api/jobs", json={"kind": "fx_scan", "params": {"market": "JP"}}, headers=AUTH)
    assert r.status_code == 400
    r = client.post("/api/jobs", json={"kind": "fx_info", "extra": 1}, headers=AUTH)
    assert r.status_code == 422


def test_post_job_enqueues_and_duplicates_409(project, monkeypatch):
    client, _ = _client(project)
    _alive(monkeypatch, True)
    body = {"kind": "fx_scan", "params": {"market": "KR"}}
    r = client.post("/api/jobs", json=body, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "queued" and r.json()["label"] == "시장 스캔"
    r2 = client.post("/api/jobs", json=body, headers=AUTH)
    assert r2.status_code == 409 and "이미" in r2.json()["detail"]
    g = client.get("/api/jobs/requests").json()
    assert g["alive"] is True and g["active"] == 1
    assert g["requests"][0]["status"] == "queued" and g["requests"][0]["source"] == "web"
    assert g["requests"][0]["label"] == "시장 스캔"
    assert g["requests"][0]["params"] == {"market": "KR"}


def test_cancel_only_queued(project, monkeypatch):
    client, deps = _client(project)
    _alive(monkeypatch, True)
    rid = client.post("/api/jobs", json={"kind": "fx_info"}, headers=AUTH).json()["id"]
    assert client.delete(f"/api/jobs/{rid}").status_code == 401
    r = client.delete(f"/api/jobs/{rid}", headers=AUTH)
    assert r.status_code == 204
    assert client.get("/api/jobs/requests").json()["requests"][0]["status"] == "skipped"
    rid2 = client.post("/api/jobs", json={"kind": "fx_info"}, headers=AUTH).json()["id"]
    conn = deps.conn_factory()
    jobq.claim_next(conn, now=NOW, kinds=["fx_info"])
    conn.close()
    assert client.delete(f"/api/jobs/{rid2}", headers=AUTH).status_code == 409


def test_trading_kind_on_real_account_requires_confirm(project, monkeypatch):
    client, deps = _client(project)
    _alive(monkeypatch, True)
    paper = {"kind": "fx_order", "params": {"account_id": "paper-1"}}
    assert client.post("/api/jobs", json=paper, headers=AUTH).status_code == 200
    real = {"kind": "fx_order", "params": {"account_id": "real-1"}}
    r = client.post("/api/jobs", json=real, headers=AUTH)
    assert r.status_code == 400 and "confirm" in r.json()["detail"]
    ok = client.post("/api/jobs", json={**real, "confirm": "real-1"}, headers=AUTH)
    assert ok.status_code == 200
    conn = deps.conn_factory()
    rows = conn.execute("SELECT kind, params FROM job_requests ORDER BY id").fetchall()
    conn.close()
    assert [r["params"] for r in rows] == ['{"account_id": "paper-1"}', '{"account_id": "real-1"}']


def test_trading_kind_without_accounts_port_is_treated_as_real(project, monkeypatch):
    """포트 없음·모르는 계좌는 실전으로 본다(fail-safe) — confirm 없으면 400."""
    _alive(monkeypatch, True)
    deps = ledger_deps(project, features=[_feature()])
    client = make_client(deps)
    body = {"kind": "fx_order", "params": {"account_id": "paper-1"}}
    assert client.post("/api/jobs", json=body, headers=AUTH).status_code == 400
    client2 = make_client(replace(deps, ports={"accounts": _Accounts()}))
    unknown = {"kind": "fx_order", "params": {"account_id": "ghost"}}
    assert client2.post("/api/jobs", json=unknown, headers=AUTH).status_code == 400
    ok = client2.post("/api/jobs", json={**unknown, "confirm": "ghost"}, headers=AUTH)
    assert ok.status_code == 200


class _PaperOnly:
    def accounts(self):
        return [SimpleNamespace(id="paper-1", env="paper")]


def test_trading_kind_without_account_id_needs_a_real_account_confirm(project, monkeypatch):
    """대상 계좌가 없는 trading kind: 실전 계좌가 하나라도 있으면 confirm 이 실전 계좌 id 중
    하나여야 한다(T3-R4). 모의뿐이면 confirm 없이 받고, 포트가 없으면 거절(fail-safe)."""
    _alive(monkeypatch, True)
    client, _ = _client(project)
    body = {"kind": "fx_all"}
    assert client.post("/api/jobs", json=body, headers=AUTH).status_code == 400
    wrong = client.post("/api/jobs", json={**body, "confirm": "paper-1"}, headers=AUTH)
    assert wrong.status_code == 400 and "confirm" in wrong.json()["detail"]
    ok = client.post("/api/jobs", json={**body, "confirm": "real-1"}, headers=AUTH)
    assert ok.status_code == 200
    deps = ledger_deps(project, features=[_feature()], ports={"accounts": _PaperOnly()})
    paper_only = make_client(deps)
    assert paper_only.delete(f"/api/jobs/{ok.json()['id']}", headers=AUTH).status_code == 204
    assert paper_only.post("/api/jobs", json=body, headers=AUTH).status_code == 200
    no_port = make_client(replace(deps, ports={}))
    assert no_port.post("/api/jobs", json={**body, "confirm": "x"}, headers=AUTH).status_code == 400


class _WithFo:
    def accounts(self):
        return [SimpleNamespace(id="paper-1", env="paper")]

    def fo_accounts(self):
        return [
            SimpleNamespace(id="fo-paper", env="paper"),
            SimpleNamespace(id="fo-real", env="real"),
        ]


def test_trading_kind_resolves_fo_account_env(project, monkeypatch):
    """`account_env` 는 파생 계좌(`fo_accounts()`)도 본다(M7): 모의 파생은 confirm 없이, 실전 파생은
    confirm 필요. 대상 없는 trading kind 도 실전 파생 계좌를 실전으로 센다."""
    _alive(monkeypatch, True)
    client, _ = _client(project, ports={"accounts": _WithFo()})
    paper = {"kind": "fx_order", "params": {"account_id": "fo-paper"}}
    assert client.post("/api/jobs", json=paper, headers=AUTH).status_code == 200
    real = {"kind": "fx_order", "params": {"account_id": "fo-real"}}
    assert client.post("/api/jobs", json=real, headers=AUTH).status_code == 400
    confirmed = client.post("/api/jobs", json={**real, "confirm": "fo-real"}, headers=AUTH)
    assert confirmed.status_code == 200
    assert client.post("/api/jobs", json={"kind": "fx_all"}, headers=AUTH).status_code == 400
    ok = client.post("/api/jobs", json={"kind": "fx_all", "confirm": "fo-real"}, headers=AUTH)
    assert ok.status_code == 200


def test_get_jobs_lists_kind_registry_and_schedule_table(project):
    client, _ = _client(project)
    r = client.get("/api/jobs").json()
    kinds = {k["kind"]: k for k in r["kinds"]}
    assert {"fx_scan", "fx_order", "fx_info", "usage_probe"} <= set(kinds)
    assert kinds["fx_order"] == {
        "kind": "fx_order",
        "label": "계좌 작업",
        "lane": "fast",
        "trading": True,
        "params_schema": _Account.model_json_schema(),
    }
    ids = {j["id"]: j for j in r["jobs"]}
    assert ids["kr_fx_scan"] == {
        "id": "kr_fx_scan",
        "kind": "fx_scan",
        "trigger": "cron",
        "schedule_key": "kr.fx_scan",
        "interval_s": None,
        "next_at": None,
    }
    assert "us_fx_scan" in ids and ids["usage_probe"]["interval_s"] == 1800


def _control_log(deps) -> list[tuple]:
    conn = deps.conn_factory()
    try:
        return [
            tuple(r)
            for r in conn.execute(
                "SELECT channel, actor, key, old, new FROM control_log ORDER BY id"
            )
        ]
    finally:
        conn.close()


def test_post_and_cancel_job_leave_control_log(project, monkeypatch):
    """조작 이력(스펙 §5): 요청은 `job:<kind>` new={request_id, params}, 취소는
    queued → cancelled."""
    import json

    client, deps = _client(project)
    _alive(monkeypatch, True)
    r = client.post("/api/jobs", json={"kind": "fx_scan", "params": {"market": "KR"}}, headers=AUTH)
    rid = r.json()["id"]
    rows = _control_log(deps)
    assert len(rows) == 1
    channel, actor, key, old, new = rows[0]
    assert (channel, key, old) == ("web", "job:fx_scan", None)
    assert actor and "secret" not in actor  # 토큰 지문만
    assert json.loads(new) == {"request_id": rid, "params": {"market": "KR"}}
    assert client.delete(f"/api/jobs/{rid}", headers=AUTH).status_code == 204
    rows = _control_log(deps)
    assert len(rows) == 2
    assert rows[1][2:] == ("job:fx_scan", json.dumps("queued"), json.dumps("cancelled"))
    # 거부된 요청·실패한 취소는 남기지 않는다
    client.post("/api/jobs", json={"kind": "nope"}, headers=AUTH)
    assert client.delete(f"/api/jobs/{rid}", headers=AUTH).status_code == 409
    assert len(_control_log(deps)) == 2
    assert "secret" not in repr(_control_log(deps))
