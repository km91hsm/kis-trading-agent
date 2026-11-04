"""merge.mount: 여러 기능의 같은 경로 ResourcePart 를 하나의 GET 으로 합친다."""

from __future__ import annotations

import logging

from tests.kernel.web.conftest import make_client, make_deps
from trader.kernel.registry import Feature, ResourcePart


def test_merge_joins_parts_sorted_and_isolates_failures(project, caplog):
    a = Feature(
        id="a",
        tier="service",
        resources=(
            ResourcePart("/decisions", lambda d, q: [{"id": 1, "at": "2026-11-04T01:00:00+09:00"}]),
        ),
    )
    b = Feature(
        id="b",
        tier="lane",
        resources=(
            ResourcePart(
                "/decisions",
                lambda d, q: [{"id": 2, "at": "2026-11-04T02:00:00+09:00"}, {"id": 3}],
            ),
        ),
    )
    c = Feature(id="c", tier="lane", resources=(ResourcePart("/decisions", lambda d, q: 1 / 0),))
    client = make_client(make_deps(project, features=[a, b, c]))
    with caplog.at_level(logging.WARNING, logger="trader.web"):
        r = client.get("/api/decisions?date=2026-11-04").json()
    assert [x["id"] for x in r["items"]] == [2, 1, 3]  # at 역순, at 없는 항목은 끝
    assert r["partial"] is True
    assert any("/decisions" in rec.getMessage() for rec in caplog.records)


def test_merge_all_parts_ok_is_not_partial(project):
    a = Feature(id="a", tier="service", resources=(ResourcePart("/x", lambda d, q: []),))
    r = make_client(make_deps(project, features=[a])).get("/api/x").json()
    assert r == {"items": [], "partial": False}


def test_merge_passes_query_and_path_params(project):
    seen: list[dict] = []

    def part(deps, q):
        seen.append(dict(q))
        assert deps is client.app.state.deps
        return [{"account_id": q["account_id"]}]

    path = "/accounts/{account_id}/positions"
    a = Feature(id="a", tier="service", resources=(ResourcePart(path, part),))
    b = Feature(id="b", tier="lane", resources=(ResourcePart(path, part),))
    client = make_client(make_deps(project, features=[a, b]))
    r = client.get("/api/accounts/acc1/positions?market=KR").json()
    assert r == {"items": [{"account_id": "acc1"}, {"account_id": "acc1"}], "partial": False}
    assert seen == [{"market": "KR", "account_id": "acc1"}] * 2
    assert len([rt for rt in client.app.routes if getattr(rt, "path", "") == "/api" + path]) == 1
