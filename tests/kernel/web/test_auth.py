"""인증 3등급 의존성: 토큰·직접 로컬·같은 출처. 쓰기 엔드포인트가 없어 테스트 전용 라우터를 건다."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends

from trader.kernel.web.app import create_app
from trader.kernel.web.auth import require_direct_local, require_same_origin, require_token
from trader.kernel.web.deps import WebDeps

LOCAL = ("127.0.0.1", 50000)


def _probe_app(deps: WebDeps):
    router = APIRouter()
    router.post("/t/token", dependencies=[Depends(require_token)])(lambda: {"ok": True})
    router.post("/t/local", dependencies=[Depends(require_direct_local)])(lambda: {"ok": True})
    router.post("/t/origin", dependencies=[Depends(require_same_origin)])(lambda: {"ok": True})
    app = create_app(deps)
    app.include_router(router, prefix="/api")
    return app


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
