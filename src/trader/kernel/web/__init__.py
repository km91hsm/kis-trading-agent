"""웹 셸: 인증 3등급·루프백 Host 게이트·기능 라우터 마운트·SSE 지문 조립. 기능 이름은 모른다."""

from __future__ import annotations

from trader.kernel.web.app import create_app
from trader.kernel.web.auth import (
    is_direct_local,
    require_direct_local,
    require_same_origin,
    require_token,
)
from trader.kernel.web.deps import WebDeps

__all__ = [
    "WebDeps",
    "create_app",
    "is_direct_local",
    "require_direct_local",
    "require_same_origin",
    "require_token",
]
