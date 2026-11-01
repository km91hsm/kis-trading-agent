"""FastAPI 앱 조립: 루프백 Host 게이트 + 기능 라우터 마운트 + health + SSE + 정적 서빙."""

from __future__ import annotations

import logging
from importlib import metadata
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from trader.kernel.web import sse
from trader.kernel.web.deps import WebDeps

logger = logging.getLogger("trader.web")

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

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


def _hostname(value: str) -> str:
    try:
        return (urlsplit("//" + value).hostname or "").lower()
    except ValueError:
        return ""


def create_app(deps: WebDeps) -> FastAPI:
    app = FastAPI(title="trader", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.deps = deps
    allowed = {*LOOPBACK_HOSTS, *(_hostname(h) or h.lower() for h in deps.extra_hosts)}

    # 로컬 전용: DNS 리바인딩으로 토큰 없는 조회 API 를 읽지 못하게 Host 를 제한한다
    @app.middleware("http")
    async def loopback_host_gate(request: Request, call_next):
        if _hostname(request.headers.get("host") or "") not in allowed:
            return JSONResponse(
                {"detail": "허용되지 않은 Host 입니다 (로컬 전용)"}, status_code=403
            )
        return await call_next(request)

    @app.get("/api/system/health")
    def health() -> dict:
        return {"ok": True, "version": _version(), "features": [f.id for f in deps.features]}

    app.include_router(sse.router, prefix="/api")
    for feature in deps.features:
        for router in feature.routes:
            app.include_router(router, prefix="/api")

    @app.get("/api/{rest:path}", include_in_schema=False)
    def _api_404(rest: str) -> JSONResponse:
        return JSONResponse({"detail": f"없는 API: /api/{rest}"}, status_code=404)

    dist = deps.dist.resolve()
    if (dist / "index.html").is_file():

        @app.get("/{rest:path}", include_in_schema=False)
        def _spa(rest: str) -> FileResponse:
            candidate = dist / rest if rest else None
            if (
                candidate is not None
                and candidate.is_file()
                and dist in candidate.resolve().parents
            ):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    else:

        @app.get("/{rest:path}", include_in_schema=False)
        def _notice(rest: str) -> HTMLResponse:
            return HTMLResponse(NO_DIST_HTML)

    return app
