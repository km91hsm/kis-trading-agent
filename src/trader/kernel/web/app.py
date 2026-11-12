"""FastAPI 앱 조립: Host·tailnet 게이트 + 서버 시각 헤더 + 커널 자원(system·jobs·runs) + SSE
+ 기능 라우터·merge 마운트 + 정적 서빙."""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from trader.kernel.web import merge, sse
from trader.kernel.web.auth import _host_of
from trader.kernel.web.deps import WebDeps
from trader.kernel.web.routers import KERNEL_ROUTERS

logger = logging.getLogger("trader.web")

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
API_METHODS = ["GET", "POST", "PATCH", "DELETE", "PUT"]
LOGIN_HEADER = "tailscale-user-login"

NO_DIST_HTML = (
    "<!doctype html><html lang='ko'><meta charset='utf-8'><body style='font-family:sans-serif'>"
    "<h2>trader 웹 UI</h2><p>web/dist 가 없습니다. 프런트를 빌드하세요:</p>"
    "<pre>cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>"
    "<p>API 는 <a href='/api/system/health'>/api/system/health</a> 로 확인할 수 있습니다.</p>"
    "</body></html>"
)


def _forbidden(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=403)


# tailscale 이 기기에 주는 주소 대역(CGNAT·ULA)
_TAILNET_NETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def _tailnet_peer(request: Request) -> bool:
    """요청을 보낸 상대(TCP peer)가 루프백이거나 tailnet 주소인가. 주소를 알 수 없으면(유닉스
    소켓·테스트 클라이언트) 막지 않는다 — 실제 서버(uvicorn TCP)는 항상 IP 를 준다."""
    peer = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or any(ip in net for net in _TAILNET_NETS)


def _gate(request: Request, allowed: set[str], users: set[str]) -> Response | None:
    """Host·tailnet 로그인 게이트. 통과면 None, 막으면 403 응답.

    DNS 리바인딩으로 토큰 없는 조회 API 를 읽지 못하게 Host 를 루프백·tailnet 허용 목록으로
    제한한다. userinfo(`@`)가 든 Host 는 파서마다 해석이 달라 항상 거부한다. 루프백이 아닌
    Host 는 `tailscale_users` 가 있으면 `Tailscale-User-Login` 이 그 목록에 있어야 한다."""
    raw = request.headers.get("host") or ""
    host = _host_of(raw)
    if "@" in raw or host not in allowed:
        return _forbidden("허용되지 않은 Host 입니다 (로컬·tailnet 전용)")
    if host not in LOOPBACK_HOSTS and users:
        # 로그인 헤더는 tailscale serve(루프백 프록시)·tailnet 주소에서 온 요청에서만 믿는다 —
        # 0.0.0.0 으로 열린 서버에 LAN 의 다른 기기가 Host 와 헤더를 지어내 붙지 못하게
        if not _tailnet_peer(request):
            return _forbidden("tailnet 밖에서 온 요청입니다 (로컬·tailnet 전용)")
        login = (request.headers.get(LOGIN_HEADER) or "").strip().lower()
        if login not in users:
            return _forbidden("허용되지 않은 tailnet 사용자입니다")
    return None


def _spa_response(dist: Path, rest: str) -> Response:
    """dist 는 요청마다 확인한다 (서버를 띄운 뒤 빌드해도 재시작 없이 서빙)."""
    dist = dist.resolve()
    index = dist / "index.html"
    if not index.is_file():
        return HTMLResponse(NO_DIST_HTML)
    candidate = dist / rest if rest else None
    if (
        candidate is not None
        and candidate.is_file()
        and dist in candidate.resolve().parents
        and candidate.resolve() != index.resolve()
    ):
        return FileResponse(candidate)
    # index.html 은 매번 재검증한다 — 앱 업데이트 뒤 옛 자산 해시를 가리키는 옛 화면이 남지 않게.
    # 해시 이름 자산(assets/*-<hash>.js|css)은 이름이 바뀌므로 지금처럼 둔다.
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _reload_settings(app: FastAPI) -> None:
    """settings.yaml 이 바뀌었고 검증을 통과하면 deps 의 settings 를 바꾼다(실패는 이전 설정)."""
    deps = app.state.deps
    watch = getattr(deps, "settings_watch", None)
    if watch is None:
        return
    try:
        new = watch.poll()
    except Exception:  # noqa: BLE001 - 감시 실패가 요청을 막지 않는다
        logger.exception("settings.yaml 변경 확인 실패")
        return
    if new is not None:
        app.state.deps = replace(deps, settings=new)


def create_app(deps: WebDeps) -> FastAPI:
    app = FastAPI(title="trader", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.deps = deps
    allowed = {*LOOPBACK_HOSTS, *(_host_of(h) or h.lower() for h in deps.extra_hosts)}
    users = {u.strip().lower() for u in deps.tailscale_users if u.strip()}

    @app.middleware("http")
    async def shell_gate(request: Request, call_next):
        _reload_settings(request.app)
        response = _gate(request, allowed, users) or await call_next(request)
        response.headers["X-Server-Time"] = request.app.state.deps.now().isoformat()
        return response

    app.include_router(sse.router, prefix="/api")
    for router in KERNEL_ROUTERS:
        app.include_router(router, prefix="/api")
    for feature in deps.features:
        for router in feature.routes:
            app.include_router(router, prefix="/api")
    merge.mount(app, deps.features)

    @app.api_route("/api", methods=API_METHODS, include_in_schema=False)
    @app.api_route("/api/{rest:path}", methods=API_METHODS, include_in_schema=False)
    def _api_404(request: Request) -> JSONResponse:
        return JSONResponse(
            {"detail": f"없는 API 경로입니다: {request.url.path}"}, status_code=404
        )

    @app.get("/{rest:path}", include_in_schema=False)
    def _spa(rest: str) -> Response:
        return _spa_response(deps.dist, rest)

    return app
