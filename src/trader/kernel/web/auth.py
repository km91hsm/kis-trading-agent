"""제어 API 인증 3등급 의존성. 토큰 미설정이면 제어 API 는 닫힌다."""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

PROXY_HEADERS = ("tailscale-user-login", "x-forwarded-for", "forwarded", "x-real-ip")


def _host_of(value: str) -> str:
    try:
        return (urlsplit("//" + value).hostname or "").lower()
    except ValueError:
        return ""


def is_direct_local(request: Request) -> bool:
    """이 PC 의 브라우저가 직접 연 요청인가: 루프백 클라이언트 ∧ 프록시 흔적 없음 ∧ Host 가
    루프백(tailscale serve 로 들어온 폰 요청은 아니다). 키 입력·토큰 받기는 이때만."""
    client = request.client.host if request.client else ""
    host = _host_of(request.headers.get("host") or "")
    return (
        client in ("127.0.0.1", "::1")
        and host in ("127.0.0.1", "localhost", "::1")
        and not any(request.headers.get(h) for h in PROXY_HEADERS)
    )


def require_direct_local(request: Request) -> None:
    if not is_direct_local(request):
        raise HTTPException(status_code=403, detail="이 PC 에서 직접 연 화면에서만 할 수 있습니다")


def require_same_origin(request: Request) -> None:
    """Origin(없으면 Referer)의 호스트가 요청 Host 와 같아야 한다 (CSRF 방어)."""
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    try:
        netloc = urlsplit(source).netloc.lower()
    except ValueError:
        netloc = ""
    if not netloc or netloc != (request.headers.get("host") or "").lower():
        raise HTTPException(status_code=403, detail="같은 출처의 화면에서만 할 수 있습니다")


def require_token(request: Request) -> None:
    token = request.app.state.deps.token
    header = request.headers.get("authorization", "")
    given = header[7:] if header.lower().startswith("bearer ") else ""
    if (
        not token
        or not given
        or not hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8"))
    ):
        raise HTTPException(
            status_code=401, detail="토큰이 필요합니다 (Authorization: Bearer <WEB_TOKEN>)"
        )


def require_confirm(body: dict, account_id: str, env: str) -> None:
    """실전(env != paper) 계좌 동작은 본문 `confirm` 에 계좌 id 를 그대로 적어야 한다."""
    if env != "paper" and body.get("confirm") != account_id:
        raise HTTPException(
            status_code=400, detail="실전 계좌 동작은 confirm 에 계좌 id 를 적어야 합니다"
        )


def actor_of(request: Request) -> str:
    """제어 변경 기록용 웹 행위자: 설정 토큰의 SHA-256 지문 앞 8자(토큰 자체는 남기지 않는다).
    `require_token` 을 지난 요청에서만 부른다."""
    token = request.app.state.deps.token or ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
