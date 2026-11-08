"""스펙 §2.5 웹 API 대응표 100% 검사: 발견된 기능 전부로 만든 앱의 OpenAPI 경로가 12 자원 표와
이 플랜(§Global)의 추가 경로를 모두 갖고, 모든 경로가 12 자원 중 하나 아래에 있는가.

경로 변수 이름은 `{…}` 로 정규화해 비교한다(`{id}` ≡ `{account_id}`). 스펙 이름에서 바뀐 경로
(`/decisions/{spot|fo}/{id}`·`/analytics/rl/*`·`/analytics/fo/*`·`/system/health` 등)는 구현
이름으로 적고, 차이는 `docs/architecture.md` web 절 「스펙 §2.5 대비 알려진 차이」에 남긴다."""

from __future__ import annotations

import re

from tests.features.conftest import web_client
from trader.kernel.registry import discover

RESOURCES = (
    "accounts", "positions", "decisions", "orders", "approvals", "jobs",
    "events", "news", "quotes", "settings", "system", "analytics",
)  # fmt: skip

# 첫 경로 세그먼트 → 자원. 스펙 §2.5 는 /ml·/fo·/names 등을 자원 아래에 묶지만 구현 경로는 자기
# 첫 세그먼트를 유지한다 — 대응은 플랜 docs/superpowers/plans/2026-11-04-m11-web-api.md
# 「자원 소유표(요약)」을 따른다(M11 Ruling T12-R1). `accounts/{id}/positions`·`/tax-lots` 는
# positions 자원이지만 첫 세그먼트가 accounts 라 accounts 로 잡힌다(두 자원 모두 유효).
SEGMENT_RESOURCE = {
    **{r: r for r in RESOURCES},
    "markets": "decisions",
    "risk-checks": "decisions",
    "fo": "orders",  # /fo/legs·/fo/summary·/fo/today
    "names": "quotes",
    "charts": "quotes",
    "features": "quotes",
    "macro": "quotes",  # ingest가 소유한 저장 매크로 관측 읽기
    "ml": "quotes",
    "runs": "jobs",
    "calls": "jobs",
}

EXPECTED = [
    # accounts
    ("GET", "/api/accounts"),
    ("POST", "/api/accounts"),
    ("GET", "/api/accounts/overview"),  # 플랜 §Global 추가
    ("PATCH", "/api/accounts/{id}"),
    ("DELETE", "/api/accounts/{id}"),
    # positions
    ("GET", "/api/accounts/{id}/positions"),
    ("GET", "/api/positions/overview"),
    ("GET", "/api/positions/risk"),
    ("GET", "/api/accounts/{id}/tax-lots"),
    # decisions
    ("GET", "/api/decisions"),
    ("GET", "/api/decisions/spot/{id}"),
    ("GET", "/api/decisions/spot/{id}/trace"),
    ("GET", "/api/decisions/spot/{id}/inputs"),
    ("GET", "/api/decisions/spot/{id}/outcomes"),
    ("GET", "/api/decisions/spot/{id}/checkpoints/{stage}"),
    ("GET", "/api/decisions/spot/news-refs"),
    ("GET", "/api/decisions/fo/{id}"),
    ("GET", "/api/decisions/fo/{id}/trace"),
    ("GET", "/api/markets/{market}/today"),
    ("GET", "/api/risk-checks"),
    ("GET", "/api/fo/today"),
    # orders
    ("GET", "/api/orders"),
    ("GET", "/api/fo/legs"),
    ("GET", "/api/fo/summary"),
    # approvals
    ("GET", "/api/approvals"),
    ("PATCH", "/api/approvals/{id}"),
    # jobs
    ("GET", "/api/jobs"),
    ("POST", "/api/jobs"),
    ("GET", "/api/jobs/requests"),
    ("DELETE", "/api/jobs/{id}"),
    ("GET", "/api/runs"),
    ("GET", "/api/calls"),
    # events
    ("GET", "/api/events"),
    # news
    ("GET", "/api/news"),
    ("GET", "/api/news/events"),
    ("GET", "/api/news/themes"),
    ("GET", "/api/news/labels"),
    ("GET", "/api/news/manual"),
    ("POST", "/api/news/manual"),
    # quotes (미국 시세는 별도 경로 없이 GET /quotes?market=US — Ruling R4)
    ("GET", "/api/quotes"),
    ("GET", "/api/quotes/indices"),
    ("GET", "/api/quotes/book"),
    ("GET", "/api/quotes/fo"),  # 플랜 §Global 추가
    ("GET", "/api/quotes/fx"),  # 플랜 §Global 추가
    ("GET", "/api/macro"),
    ("GET", "/api/system/market-clock"),
    ("GET", "/api/names"),
    ("GET", "/api/names/search"),
    ("GET", "/api/charts"),
    ("GET", "/api/charts/minute"),
    ("GET", "/api/features/latest"),
    ("GET", "/api/features/history"),
    # settings
    ("GET", "/api/settings"),
    ("PATCH", "/api/settings/paused"),
    ("GET", "/api/settings/watchlist"),
    ("PATCH", "/api/settings/watchlist"),
    ("PATCH", "/api/settings/usage-throttle"),
    ("PATCH", "/api/settings/session-cap"),
    ("PATCH", "/api/settings/codex-throttle"),
    ("PATCH", "/api/settings/codex-call-cap"),
    ("GET", "/api/settings/secrets"),
    ("POST", "/api/settings/secrets"),
    # system (옛 /system/ops 는 /system/health 로)
    ("GET", "/api/system/health"),
    ("GET", "/api/system/status"),
    ("GET", "/api/system/usage"),
    ("POST", "/api/system/usage/ensure-fresh"),
    ("GET", "/api/system/daemon"),
    ("GET", "/api/system/notices"),  # 플랜 §Global 추가
    ("GET", "/api/system/control-log"),  # 플랜 §Global 추가
    ("GET", "/api/system/regime"),
    ("GET", "/api/system/saveticker"),
    ("POST", "/api/system/saveticker/restart"),
    ("GET", "/api/system/scalp"),
    ("GET", "/api/system/fo"),
    ("POST", "/api/system/bootstrap-token"),
    # analytics (스펙 /ml/*·/rl/*·/fo/{scorecard,report,summary})
    ("GET", "/api/analytics/performance"),
    ("GET", "/api/analytics/performance/compare"),
    ("GET", "/api/analytics/shadow"),
    ("GET", "/api/analytics/calibration"),
    ("GET", "/api/analytics/memory"),
    ("GET", "/api/ml/models"),
    ("GET", "/api/ml/models/{version}"),
    ("GET", "/api/analytics/rl/experiments"),
    ("GET", "/api/analytics/rl/experiments/{version}"),
    ("GET", "/api/analytics/rl/report"),
    ("GET", "/api/analytics/rl/shadow"),
    ("GET", "/api/analytics/fo/scorecard"),
    ("GET", "/api/analytics/fo/report"),
    ("GET", "/api/analytics/fo/summary"),
]

_PARAM = re.compile(r"\{[^}]*\}")


def _norm(path: str) -> str:
    return _PARAM.sub("{}", path)


def _registered(tmp_path) -> set[tuple[str, str]]:
    (tmp_path / "state").mkdir(exist_ok=True)
    app = web_client(tmp_path, discover()).app
    return {
        (method.upper(), _norm(path))
        for path, ops in app.openapi()["paths"].items()
        for method in ops
    }


def test_spec_2_5_paths_all_registered(tmp_path):
    assert len(EXPECTED) == len(set(EXPECTED))
    registered = _registered(tmp_path)
    missing = [(m, p) for m, p in EXPECTED if (m, _norm(p)) not in registered]
    assert missing == []


def test_every_feature_route_is_under_a_resource(tmp_path):
    assert set(SEGMENT_RESOURCE.values()) == set(RESOURCES)
    stray = sorted(
        path
        for _, path in _registered(tmp_path)
        if not path.startswith("/api/") or path.split("/")[2] not in SEGMENT_RESOURCE
    )
    assert stray == []
