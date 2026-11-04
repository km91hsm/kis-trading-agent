"""인증 3등급 의존성: 토큰·직접 로컬·같은 출처. 등급별 검사는 테스트 전용 라우터로 건다."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends

from tests.kernel.web.conftest import make_client, make_deps
from trader.kernel.registry import Feature
from trader.kernel.web.app import create_app
from trader.kernel.web.auth import require_direct_local, require_same_origin, require_token
from trader.kernel.web.deps import WebDeps

LOCAL = ("127.0.0.1", 50000)


def _probe_app(deps: WebDeps):
    router = APIRouter()
    router.post("/t/token", dependencies=[Depends(require_token)])(lambda: {"ok": True})
    router.post("/t/local", dependencies=[Depends(require_direct_local)])(lambda: {"ok": True})
    router.post("/t/origin", dependencies=[Depends(require_same_origin)])(lambda: {"ok": True})
    # 기능 라우터로 건다: create_app 뒤에 붙이면 `/api/{rest:path}` 404 가 먼저 잡는다
    return create_app(replace(deps, features=[Feature(id="t", tier="service", routes=(router,))]))


def _client(deps: WebDeps, **kw):
    from fastapi.testclient import TestClient

    return TestClient(_probe_app(deps), base_url="http://127.0.0.1", **kw)


def test_token_missing_wrong_right(deps):
    c = _client(deps, client=LOCAL)
    assert c.post("/api/t/token").status_code == 401
    assert c.post("/api/t/token", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.post("/api/t/token", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_token_unset_closes_control(deps):
    c = _client(replace(deps, token=None), client=LOCAL)
    assert c.post("/api/t/token", headers={"Authorization": "Bearer secret"}).status_code == 401
    assert c.post("/api/t/token", headers={"Authorization": "Bearer "}).status_code == 401


def test_direct_local_ok_for_loopback_client(deps):
    c = _client(deps, client=LOCAL)
    assert c.post("/api/t/local").status_code == 200


def test_direct_local_rejects_remote_client(deps):
    c = _client(deps, client=("10.0.0.5", 1234))
    assert c.post("/api/t/local").status_code == 403


def test_direct_local_rejects_forwarded(deps):
    c = _client(deps, client=LOCAL)
    for h in ("x-forwarded-for", "tailscale-user-login", "forwarded", "x-real-ip"):
        assert c.post("/api/t/local", headers={h: "1.2.3.4"}).status_code == 403, h


def test_direct_local_rejects_known_proxy_headers_even_when_empty(deps):
    """알려진 프록시 헤더는 값이 비어도 존재만으로 프록시 경유로 본다(M1)."""
    c = _client(deps, client=LOCAL)
    for h in ("x-forwarded-for", "tailscale-user-login", "forwarded", "x-real-ip"):
        assert c.post("/api/t/local", headers={h: ""}).status_code == 403, h


def test_direct_local_rejects_any_x_forwarded_header(deps):
    """프록시가 붙이는 `x-forwarded-*` 는 무엇이든 루프백 판정을 끈다(Host·Proto·Port 포함)."""
    c = _client(deps, client=LOCAL)
    for h in ("x-forwarded-host", "x-forwarded-proto", "x-forwarded-port", "X-Forwarded-Server"):
        assert c.post("/api/t/local", headers={h: "127.0.0.1"}).status_code == 403, h


def test_same_origin(deps):
    c = _client(deps, client=LOCAL)
    ok = c.post(
        "/api/t/origin", headers={"Origin": "http://127.0.0.1:8787", "Host": "127.0.0.1:8787"}
    )
    assert ok.status_code == 200
    bad = c.post(
        "/api/t/origin", headers={"Origin": "http://evil.example", "Host": "127.0.0.1:8787"}
    )
    assert bad.status_code == 403
    ref = c.post(
        "/api/t/origin", headers={"Referer": "http://127.0.0.1:8787/x", "Host": "127.0.0.1:8787"}
    )
    assert ref.status_code == 200
    assert c.post("/api/t/origin").status_code == 403


TAILNET = "mac.tailnet.ts.net"


def test_tailnet_login_allowlist_and_direct_local_stays_false(project):
    deps = make_deps(
        project,
        extra_hosts=(TAILNET,),
        tailscale_users=("me@example.com",),
    )
    c = make_client(deps)
    ok = c.get(
        "/api/system/health", headers={"host": TAILNET, "tailscale-user-login": "me@example.com"}
    )
    assert ok.status_code == 200
    upper = c.get(
        "/api/system/health", headers={"host": TAILNET, "tailscale-user-login": "ME@Example.com"}
    )
    assert upper.status_code == 200  # 로그인 비교는 대소문자 무시
    bad = c.get(
        "/api/system/health",
        headers={"host": TAILNET, "tailscale-user-login": "other@example.com"},
    )
    assert bad.status_code == 403
    none = c.get("/api/system/health", headers={"host": TAILNET})
    assert none.status_code == 403  # 허용 목록이 비어 있지 않으면 로그인 헤더가 있어야 한다
    key = c.post(
        "/api/system/bootstrap-token",
        headers={
            "host": TAILNET,
            "tailscale-user-login": "me@example.com",
            "origin": f"https://{TAILNET}",
        },
    )
    assert key.status_code == 403  # 직접 로컬 아님


def test_loopback_host_needs_no_tailnet_login(project):
    deps = make_deps(project, extra_hosts=(TAILNET,), tailscale_users=("me@example.com",))
    assert make_client(deps).get("/api/system/health").status_code == 200


def test_empty_tailscale_users_trusts_tailnet(project):
    c = make_client(make_deps(project, extra_hosts=(TAILNET,)))
    assert c.get("/api/system/health", headers={"host": TAILNET}).status_code == 200
    assert c.get("/api/system/health", headers={"host": "evil.example"}).status_code == 403


def test_host_userinfo_rejected(client):
    assert client.get("/api/system/health", headers={"host": "a@127.0.0.1"}).status_code == 403


def test_require_confirm_only_for_real_accounts():
    import pytest
    from fastapi import HTTPException

    from trader.kernel.web.auth import require_confirm

    require_confirm({}, "acc1", "paper")
    require_confirm({"confirm": "acc1"}, "acc1", "real")
    for body in ({}, {"confirm": "acc2"}, {"confirm": True}):
        with pytest.raises(HTTPException) as e:
            require_confirm(body, "acc1", "real")
        assert e.value.status_code == 400
        assert e.value.detail == "실전 계좌 동작은 confirm 에 계좌 id 를 적어야 합니다"
