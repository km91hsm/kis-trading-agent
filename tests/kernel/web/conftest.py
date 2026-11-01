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


@pytest.fixture
def deps(project: Path) -> WebDeps:
    db = project / "state" / "ledger.db"
    return WebDeps(
        settings=SimpleNamespace(),
        conn_factory=lambda: connect(db),
        state=project / "state",
        token="secret",
        dist=project / "dist",
        now=lambda: NOW,
    )


@pytest.fixture
def deps_with_feature(deps: WebDeps) -> WebDeps:
    return replace(deps, features=[fixture_feature()])


def make_client(deps: WebDeps, **kw) -> TestClient:
    return TestClient(create_app(deps), base_url="http://127.0.0.1", **kw)


@pytest.fixture
def client(deps: WebDeps) -> TestClient:
    return make_client(deps)
