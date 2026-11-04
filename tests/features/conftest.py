"""기능 테스트 공용 웹 도구: scratch 원장 위의 TestClient 와 쓰기 요청 헤더."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.ledger import db
from trader.kernel.platform.bundle import templates_dir
from trader.kernel.registry import Feature, discover
from trader.kernel.registry.contracts import all_migrations
from trader.kernel.web.app import create_app
from trader.kernel.web.deps import WebDeps

# 쓰기 엔드포인트용: web_client 기본 토큰 + TestClient 기본 Host(testserver)와 같은 출처
AUTH = {"Authorization": "Bearer t", "Origin": "http://testserver"}


def load_settings(project: Path):
    """project/config/settings.yaml (없으면 템플릿)을 모든 기능 섹션으로 읽는다."""
    path = project / "config" / "settings.yaml"
    if not path.exists():
        path = templates_dir() / "config" / "settings.yaml"
    return load("settings", build_registry(discover()), path=path)


def web_client(
    project: Path,
    features: list[Feature],
    *,
    token: str | None = "t",
    now: datetime | Callable[[], datetime] | None = None,
    ports: Mapping | None = None,
) -> TestClient:
    """주어진 기능만 마운트한 웹 앱의 TestClient. 원장은 project/state/trader.db 에
    모든 마이그레이션을 적용해 둔다. Host 는 TestClient 기본값 testserver 를 허용한다.
    ports 는 `WebDeps.ports`(예: `{"accounts": …}`)."""
    ledger = project / "state" / "trader.db"
    conn = db.connect(ledger)
    try:
        db.init_schema(conn, all_migrations(discover()))
    finally:
        conn.close()
    if isinstance(now, datetime):
        fixed = now
        now = lambda: fixed  # noqa: E731
    deps = WebDeps(
        settings=load_settings(project),
        conn_factory=lambda: db.connect(ledger),
        state=project / "state",
        token=token,
        dist=project / "nodist",
        now=now or (lambda: datetime.now(UTC)),
        extra_hosts=("testserver",),
        tailscale_users=(),
        public_base=None,
        features=features,
        ports=dict(ports or {}),
    )
    return TestClient(create_app(deps))
