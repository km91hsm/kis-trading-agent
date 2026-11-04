"""공용 web_client 도구 자체 점검: health 조회·토큰 쓰기 헤더가 통과한다."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from tests.features.conftest import AUTH, web_client
from trader.kernel.registry import Feature
from trader.kernel.web.auth import require_same_origin, require_token


def test_web_client_reads_and_auth_headers_pass_write_tier(project):
    router = APIRouter()
    router.post("/w", dependencies=[Depends(require_token), Depends(require_same_origin)])(
        lambda: {"ok": True}
    )
    c = web_client(project, [Feature(id="w", tier="service", routes=(router,))])
    assert c.get("/api/system/health").json()["features"] == ["w"]
    assert c.post("/api/w", headers=AUTH).json() == {"ok": True}
    assert c.post("/api/w").status_code == 401
