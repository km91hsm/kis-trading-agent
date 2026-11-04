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


def test_merge_sort_is_iso_aware_with_dates_and_none(project):
    """`at` 은 문자열이 아니라 시각으로 비교한다: 오프셋이 다른 ISO·날짜만(그날 00:00 UTC)·naive
    (UTC 로 간주)·None(끝)."""
    rows = [
        {"id": "kst", "at": "2026-11-04T08:00:00+09:00"},  # 2026-11-03T23:00Z
        {"id": "utc", "at": "2026-11-03T23:30:00+00:00"},
        {"id": "date", "at": "2026-11-04"},  # 2026-11-04T00:00Z
        {"id": "naive", "at": "2026-11-03T23:10:00"},
        {"id": "none", "at": None},
    ]
    a = Feature(id="a", tier="service", resources=(ResourcePart("/x", lambda d, q: rows),))
    r = make_client(make_deps(project, features=[a])).get("/api/x").json()
    assert [x["id"] for x in r["items"]] == ["date", "utc", "naive", "kst", "none"]


def test_merge_non_list_part_is_skipped_as_partial(project, caplog):
    a = Feature(id="a", tier="service", resources=(ResourcePart("/x", lambda d, q: {"id": 1}),))
    b = Feature(id="b", tier="lane", resources=(ResourcePart("/x", lambda d, q: [{"id": 2}]),))
    with caplog.at_level(logging.WARNING, logger="trader.web"):
        r = make_client(make_deps(project, features=[a, b])).get("/api/x").json()
    assert r == {"items": [{"id": 2}], "partial": True}
    assert any("/x" in rec.getMessage() for rec in caplog.records)


def test_merge_format_csv_returns_csv(project):
    rows = [{"id": 1, "at": "2026-11-04T01:00:00+00:00", "x": None}]
    a = Feature(id="a", tier="service", resources=(ResourcePart("/orders", lambda d, q: rows),))
    client = make_client(make_deps(project, features=[a]))
    r = client.get("/api/orders?format=csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="orders.csv"' in r.headers["content-disposition"]
    assert r.text.lstrip("﻿").splitlines() == ["id,at,x", "1,2026-11-04T01:00:00+00:00,"]
    assert client.get("/api/orders").json()["items"] == rows


def test_merge_reraises_part_http_exception(project):
    """조각이 낸 HTTPException(예: 잘못된 date 400)은 삼키지 않고 그대로 돌려준다(M2)."""
    from fastapi import HTTPException

    def bad(d, q):
        raise HTTPException(status_code=400, detail="date 형식 오류")

    a = Feature(id="a", tier="service", resources=(ResourcePart("/x", lambda d, q: [{"id": 1}]),))
    b = Feature(id="b", tier="lane", resources=(ResourcePart("/x", bad),))
    r = make_client(make_deps(project, features=[a, b])).get("/api/x")
    assert r.status_code == 400 and r.json()["detail"] == "date 형식 오류"


def test_merge_csv_marks_partial_with_header(project):
    """CSV 는 본문에 partial 을 실을 수 없어 `X-Partial: 1` 헤더로 알린다(M2)."""
    ok = Feature(id="a", tier="service", resources=(ResourcePart("/x", lambda d, q: [{"id": 1}]),))
    boom = Feature(id="b", tier="lane", resources=(ResourcePart("/x", lambda d, q: 1 / 0),))
    full = make_client(make_deps(project, features=[ok])).get("/api/x?format=csv")
    assert full.status_code == 200 and "x-partial" not in full.headers
    part = make_client(make_deps(project, features=[ok, boom])).get("/api/x?format=csv")
    assert part.status_code == 200 and part.headers["x-partial"] == "1"
