"""web 셸 테스트 공용 픽스처: 임시 프로젝트의 WebDeps 와 로컬 TestClient."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from trader.kernel.ledger.db import connect
from trader.kernel.registry import Feature, SseKey
from trader.kernel.web.app import create_app
from trader.kernel.web.deps import WebDeps

NOW = datetime(2026, 11, 2, 1, 2, 3, tzinfo=UTC)


def fixture_feature(value=lambda: 1) -> Feature:
    router = APIRouter()

    @router.get("/fixture/ping")
    def ping() -> dict:
        return {"pong": True}

    return Feature(
        id="fixture",
        tier="service",
        routes=(router,),
        sse_keys=(SseKey("fixture_count", lambda deps: value(), noisy=True),),
    )


def make_deps(project: Path, **overrides) -> WebDeps:
    """기본 WebDeps 위에 `extra_hosts`·`tailscale_users`·`features`·`token` 등을 덮어쓴다."""
    db = project / "state" / "ledger.db"
    base = WebDeps(
        settings=SimpleNamespace(),
        conn_factory=lambda: connect(db),
        state=project / "state",
        token="secret",
        dist=project / "dist",
        now=lambda: NOW,
    )
    return replace(base, **overrides)


def ledger_deps(project: Path, **overrides) -> WebDeps:
    """커널 마이그레이션을 적용한 원장 + 커널 설정(llm·markets)을 가진 WebDeps."""
    from trader.kernel.config import LlmSettings
    from trader.kernel.ledger.db import init_schema
    from trader.kernel.registry import discover
    from trader.kernel.registry.contracts import all_migrations

    conn = connect(project / "state" / "ledger.db")
    try:
        init_schema(conn, all_migrations(discover()))  # 버전은 기능과 이어진다
    finally:
        conn.close()
    settings = SimpleNamespace(llm=LlmSettings(usage_file=None), markets=["KR", "US"])
    return make_deps(project, **{"settings": settings, **overrides})


# 쓰기 요청 헤더: make_deps 토큰 + make_client Host(127.0.0.1)와 같은 출처
AUTH = {"Authorization": "Bearer secret", "Origin": "http://127.0.0.1"}
SAME = {"Origin": "http://127.0.0.1"}


@pytest.fixture
def deps(project: Path) -> WebDeps:
    return make_deps(project)


@pytest.fixture
def deps_with_feature(deps: WebDeps) -> WebDeps:
    return replace(deps, features=[fixture_feature()])


def make_client(deps: WebDeps, **kw) -> TestClient:
    return TestClient(create_app(deps), base_url="http://127.0.0.1", **kw)


@pytest.fixture
def client(deps: WebDeps) -> TestClient:
    return make_client(deps)
