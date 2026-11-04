"""FastAPI 앱 조립: Host·tailnet 게이트 + 서버 시각 헤더 + 기능 라우터·merge 마운트 + health
+ SSE + 정적 서빙."""

from __future__ import annotations

import logging
from importlib import metadata
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from trader.kernel.web import merge, sse
from trader.kernel.web.auth import _host_of
from trader.kernel.web.deps import WebDeps

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


def _version() -> str:
    try:
        return metadata.version("trader")
    except metadata.PackageNotFoundError:
        return "0.2.0"


def _forbidden(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=403)


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
    if candidate is not None and candidate.is_file() and dist in candidate.resolve().parents:
        return FileResponse(candidate)
    return FileResponse(index)


def create_app(deps: WebDeps) -> FastAPI:
    app = FastAPI(title="trader", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.deps = deps
    allowed = {*LOOPBACK_HOSTS, *(_host_of(h) or h.lower() for h in deps.extra_hosts)}
    users = {u.strip().lower() for u in deps.tailscale_users if u.strip()}

    @app.middleware("http")
    async def shell_gate(request: Request, call_next):
        response = _gate(request, allowed, users) or await call_next(request)
        response.headers["X-Server-Time"] = request.app.state.deps.now().isoformat()
        return response

    @app.get("/api/system/health")
    def health() -> dict:
        return {"ok": True, "version": _version(), "features": [f.id for f in deps.features]}

    app.include_router(sse.router, prefix="/api")
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
