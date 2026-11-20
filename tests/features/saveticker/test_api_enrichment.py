import json
from datetime import UTC, datetime, timedelta

import pytest

from trader.features.saveticker import api_contract, jsonfeed, native
from trader.features.saveticker.models import Capture


def send(client, kind, **kwargs):
    return client.handle({"id": 1, "type": kind, **kwargs})


def response(url, data):
    return {"url": url, "status": 200, "content_type": "application/json", "data": data}


def setup(client):
    job = send(client, "api_context_begin")
    assert job["ok"]
    result = send(
        client,
        "api_context",
        job_id=job["job_id"],
        response=response(
            job["request"]["url"],
            {"groups": [{"groupId": 42, "isDefault": True, "name": "must not be persisted"}]},
        ),
    )
    assert result["ok"]
    return result


def test_default_group_is_projected_and_bound_to_rank_requests(tmp_path):
    client = native.NativeSession(tmp_path)
    assert not send(client, "api_begin", stream="market_gain_live")["ok"]
    setup(client)
    job = send(client, "api_begin", stream="market_gain_live")
    assert job["ok"]
    assert job["request"]["url"].endswith("tab=gainers&groupId=42&excludeRisky=true")
    assert not list(tmp_path.rglob("*groups*.json"))


@pytest.mark.parametrize(
    "groups",
    [
        [],
        [{"groupId": 1, "isDefault": True}, {"groupId": 2, "isDefault": True}],
        [{"groupId": "1&private=true", "isDefault": True}],
        [{"groupId": True, "isDefault": True}],
    ],
)
def test_ambiguous_or_invalid_group_does_not_enable_requests(tmp_path, groups):
    client = native.NativeSession(tmp_path)
    job = send(client, "api_context_begin")
    assert job["ok"]
    assert not send(
        client,
        "api_context",
        job_id=job["job_id"],
        response=response(job["request"]["url"], {"groups": groups}),
    )["ok"]
    assert not send(client, "api_begin", stream="market_gain_live")["ok"]


def test_news_detail_is_native_issued_correlated_and_saved(tmp_path):
    client = native.NativeSession(tmp_path)
    job = send(client, "api_begin", stream="news_all")
    base = response(
        job["request"]["url"],
        {"news_list": [{"id": "news_one", "title": "One", "source": "save-pick"}]},
    )
    prepared = send(client, "api_prepare", stream="news_all", job_id=job["job_id"], response=base)
    assert prepared["ok"]
    child = prepared["requests"][0]
    assert child["request"]["url"] == "https://www.saveticker.com/api/news/detail?id=news_one"
    detail = response(
        child["request"]["url"],
        {
            "id": "news_one",
            "title": "One",
            "source": "save-pick",
            "content": [{"type": "text", "content": "Full body"}],
        },
    )
    forged = {**detail, "data": {**detail["data"], "id": "news_other"}}
    assert not send(
        client,
        "api_snapshot",
        stream="news_all",
        job_id=job["job_id"],
        response=base,
        details=[forged],
    )["ok"]
    saved = send(
        client,
        "api_snapshot",
        stream="news_all",
        job_id=job["job_id"],
        response=base,
        details=[detail],
    )
    assert saved["ok"]
    item = json.loads((tmp_path / "data/latest/news_all.json").read_text(encoding="utf-8"))[
        "items"
    ][0]
    assert item["body"] == "Full body" and item["detail_status"] == "ok"
    job = send(client, "api_begin", stream="news_all")
    prepared = send(client, "api_prepare", stream="news_all", job_id=job["job_id"], response=base)
    assert prepared["requests"] == [], "cached complete body is not fetched every cycle"


def test_news_uses_current_article_ids_to_reuse_cached_body(tmp_path, monkeypatch):
    client = native.NativeSession(tmp_path)
    job = send(client, "api_begin", stream="news_all")
    row = {"id": "news_one", "title": "One", "source": "save-pick"}
    base = response(job["request"]["url"], {"news_list": [row]})
    pending = jsonfeed.news_item(row)
    full = pending.model_copy(update={"body": "Cached body", "detail_status": "ok"})
    observed = datetime.now(UTC) - timedelta(minutes=1)
    for article in (full, pending):
        client.store.save(
            Capture(
                stream="news_all",
                page_url="https://www.saveticker.com/news",
                observed_at=observed,
                items=[article],
            )
        )
        observed += timedelta(seconds=1)

    def reject_global_query(**kwargs):
        pytest.fail("뉴스 수집 준비와 저장은 전체 수집 이력을 조회하면 안 됩니다")

    monkeypatch.setattr(client.store, "query", reject_global_query)
    context = {"stream": "news_all", "job_id": job["job_id"], "response": base}

    prepared = send(client, "api_prepare", **context)
    assert prepared["ok"] and prepared["requests"] == []
    saved = send(client, "api_snapshot", **context, details=[])
    assert saved["ok"] and saved["count"] == 1
    snapshot = json.loads((tmp_path / "data/latest/news_all.json").read_text(encoding="utf-8"))
    assert snapshot["items"][0]["body"] == "Cached body"


def test_prepared_base_cannot_be_swapped(tmp_path):
    client = native.NativeSession(tmp_path)
    job = send(client, "api_begin", stream="news_all")
    base = response(job["request"]["url"], {"news_list": [{"id": "one", "title": "One"}]})
    assert send(client, "api_prepare", stream="news_all", job_id=job["job_id"], response=base)["ok"]
    changed = {**base, "data": {"news_list": [{"id": "two", "title": "Two"}]}}
    assert not send(
        client,
        "api_snapshot",
        stream="news_all",
        job_id=job["job_id"],
        response=changed,
        details=[],
    )["ok"]
    assert not (tmp_path / "data/latest/news_all.json").exists()


def test_report_document_is_bound_to_detail_and_existing_pdf_ticket(tmp_path):
    client = native.NativeSession(tmp_path)
    job = send(client, "api_begin", stream="news_reports")
    base = response(
        job["request"]["url"],
        {
            "reports": [{"id": "7", "title": "2026년 10월 30일 (금)", "has_pdf": True}],
            "page": 1,
            "page_size": 20,
            "total_count": 1,
        },
    )
    prepared = send(
        client, "api_prepare", stream="news_reports", job_id=job["job_id"], response=base
    )
    assert prepared["ok"]
    child = prepared["requests"][0]
    assert child["request"]["url"].endswith("/api/report/detail?id=7")
    detail = response(
        child["request"]["url"],
        {
            "report": {
                "id": "7",
                "title": "2026년 10월 30일 (금)",
                "pdf_url": "/api/uploads/documents/77",
            }
        },
    )
    saved = send(
        client,
        "api_snapshot",
        stream="news_reports",
        job_id=job["job_id"],
        response=base,
        details=[detail],
    )
    assert saved["ok"] and len(saved["pdf_items"]) == 1
    ticket = send(client, "api_pdf_begin", item_id=saved["pdf_items"][0]["id"])
    assert ticket["ok"] and ticket["ticket"] in client._tickets
    assert ticket["request"]["url"].endswith(
        "/api/report/download?url=%2Fapi%2Fuploads%2Fdocuments%2F77"
    )
    assert ticket["download_name"].endswith(".pdf")
    assert not send(client, "api_pdf_begin", item_id="unrelated")["ok"]


def test_live_snapshot_never_substitutes_for_explicit_regular_basis(tmp_path):
    client = native.NativeSession(tmp_path)
    setup(client)
    job = send(client, "api_begin", stream="market_gain_regular")
    assert job["ok"]
    data = {
        "tab": "gainers",
        "basis": "live",
        "session": "open",
        "excludeRisky": True,
        "items": [{"symbol": "AAA", "rank": 1, "price": 12, "changePercent": 3}],
    }
    bad = send(
        client,
        "api_snapshot",
        stream="market_gain_regular",
        job_id=job["job_id"],
        response=response(job["request"]["url"], {**data, "session": "closed"}),
    )
    assert not bad["ok"]
    good = send(
        client,
        "api_snapshot",
        stream="market_gain_regular",
        job_id=job["job_id"],
        response=response(job["request"]["url"], data),
    )
    assert not good["ok"]
    assert not (tmp_path / "data/latest/market_gain_regular.json").exists()


def test_popular_requires_exact_server_tab_basis_filter(tmp_path):
    client = native.NativeSession(tmp_path)
    setup(client)
    v = next(v for v in client._plan["views"] if v["stream"] == "market_gain_live")
    request = api_contract.descriptor(v, group_id=42)
    data = {
        "tab": "losers",
        "basis": "live",
        "session": "open",
        "excludeRisky": True,
        "items": [{"symbol": "AAA", "rank": 1, "price": 12, "changePercent": 3}],
    }
    with pytest.raises(ValueError):
        api_contract.parse_snapshot(v, response(request["url"], data), group_id=42)


@pytest.mark.parametrize(
    "stream,tab",
    [
        ("community_gain_regular", "gainers"),
        ("community_loss_regular", "losers"),
        ("market_gain_regular", "gainers"),
        ("market_loss_regular", "losers"),
        ("market_turnover_regular", "turnover"),
    ],
)
def test_explicit_regular_request_and_server_basis(tmp_path, stream, tab):
    client = native.NativeSession(tmp_path)
    setup(client)
    job = send(client, "api_begin", stream=stream)
    suffix = f"tab={tab}&groupId=42&basis=regular"
    if tab != "turnover":
        suffix += "&excludeRisky=true"
    assert job["request"]["url"].endswith(suffix)
    data = {
        "tab": tab,
        "basis": "regular",
        "session": "open",
        "excludeRisky": tab != "turnover",
        "asOf": "2026-10-30T20:00:00Z",
        "items": [{"symbol": "AAA", "rank": 1, "price": 12, "changePercent": 3}],
    }
    saved = send(
        client,
        "api_snapshot",
        stream=stream,
        job_id=job["job_id"],
        response=response(job["request"]["url"], data),
    )
    assert saved["ok"] and saved["count"] == 1
    latest = json.loads((tmp_path / f"data/latest/{stream}.json").read_text(encoding="utf-8"))
    assert latest["items"][0]["basis"] == "regular"
    assert latest["view"]["requested_regular"] is True


def _turnover(client, basis):
    job = send(client, "api_begin", stream="market_turnover_live")
    assert job["ok"]
    data = {
        "tab": "turnover",
        "basis": basis,
        "session": "open",
        "excludeRisky": False,
        "items": [{"symbol": "AAA", "rank": 1, "price": 12, "changePercent": 3}],
    }
    return send(
        client,
        "api_snapshot",
        stream="market_turnover_live",
        job_id=job["job_id"],
        response=response(job["request"]["url"], data),
    )


def test_rejected_response_keeps_specific_contract_code(tmp_path):
    client = native.NativeSession(tmp_path)
    setup(client)
    bad = _turnover(client, "regular")
    assert bad["ok"] is False and bad["error"] == "api_basis_unverified"


def test_api_error_codes_match_extension_runner():
    import re
    from pathlib import Path

    js = (Path(__file__).parents[3] / "extensions/saveticker/api-runner.js").read_text(
        encoding="utf-8"
    )
    codes = re.search(r"const API_ERRORS = new Set\('([^']+)'\.split", js).group(1).split(" ")
    assert set(codes) == set(api_contract.API_ERRORS)
    from trader.features.saveticker.web_status import FAILURE_TEXT

    assert set(FAILURE_TEXT) == set(api_contract.API_ERRORS)


def test_failed_attempt_is_recorded_without_touching_last_success(tmp_path):
    client = native.NativeSession(tmp_path)
    setup(client)
    assert _turnover(client, "live")["ok"]
    path = tmp_path / "data/status.json"
    before = json.loads(path.read_text(encoding="utf-8"))["streams"]["market_turnover_live"]
    assert before["last_error"] is None
    assert not _turnover(client, "regular")["ok"]
    after = json.loads(path.read_text(encoding="utf-8"))["streams"]["market_turnover_live"]
    assert after["last_error"] == "api_basis_unverified"
    assert after["last_attempt_at"] > before["last_attempt_at"]
    assert after["last_success_at"] == before["last_success_at"]
    assert after["count"] == before["count"]
    # 다른 스트림 저장이 실패 기록을 지우지 않는다
    # (실패한 작업은 같은 레인을 잠시 잡고 있으므로 다른 레인으로)
    job = send(client, "api_begin", stream="community_gain_live")
    data = {"tab": "gainers", "basis": "live", "session": "open", "excludeRisky": True,
            "items": [{"symbol": "AAA", "rank": 1, "price": 12, "changePercent": 3}]}
    assert send(client, "api_snapshot", stream="community_gain_live", job_id=job["job_id"],
                response=response(job["request"]["url"], data))["ok"]
    kept = json.loads(path.read_text(encoding="utf-8"))["streams"]["market_turnover_live"]
    assert kept["last_error"] == "api_basis_unverified"
    assert kept["last_attempt_at"] == after["last_attempt_at"]
    # 성공하면 지운다
    client._jobs.pop("market", None)
    assert _turnover(client, "live")["ok"]
    done = json.loads(path.read_text(encoding="utf-8"))["streams"]["market_turnover_live"]
    assert done["last_error"] is None and done["last_success_at"] > before["last_success_at"]


def test_failure_before_any_success_creates_error_entry(tmp_path):
    client = native.NativeSession(tmp_path)
    setup(client)
    assert not _turnover(client, "regular")["ok"]
    row = json.loads((tmp_path / "data/status.json").read_text(encoding="utf-8"))["streams"][
        "market_turnover_live"
    ]
    assert row["last_error"] == "api_basis_unverified"
    assert row["last_success_at"] is None


def test_enrich_rejection_is_recorded_as_failed_attempt(tmp_path):
    """본문 보강 단계 거절(`ApiError`, 예: api_detail_unverified)도
    실패 시도로 기록한 뒤 거절한다."""
    client = native.NativeSession(tmp_path)
    job = send(client, "api_begin", stream="news_all")
    base = response(job["request"]["url"], {"news_list": [{"id": "news_one", "title": "One"}]})
    prepared = send(client, "api_prepare", stream="news_all", job_id=job["job_id"], response=base)
    detail = response(prepared["requests"][0]["request"]["url"],
                      {"id": "news_other", "title": "One",
                       "content": [{"type": "text", "content": "Full body"}]})
    bad = send(client, "api_snapshot", stream="news_all", job_id=job["job_id"], response=base,
               details=[detail])
    assert bad["ok"] is False and bad["error"] == "api_detail_unverified"
    row = json.loads((tmp_path / "data/status.json").read_text(encoding="utf-8"))["streams"][
        "news_all"
    ]
    assert row["last_error"] == "api_detail_unverified" and row["last_success_at"] is None


def test_prepared_news_list_is_saved_without_details(tmp_path):
    client = native.NativeSession(tmp_path)
    job = send(client, "api_begin", stream="news_all")
    base = response(job["request"]["url"], {"news_list": [{"id": "one", "title": "One"}]})
    context = {"stream": "news_all", "job_id": job["job_id"], "response": base}
    prepared = send(client, "api_prepare", **context)
    assert prepared["ok"] and len(prepared["requests"]) == 1

    saved = send(client, "api_snapshot", **context, details=[])

    assert saved["ok"] and saved["count"] == 1
    snapshot = json.loads((tmp_path / "data/latest/news_all.json").read_text(encoding="utf-8"))
    assert snapshot["status"] == "partial"
    assert snapshot["items"][0]["title"] == "One"
    assert snapshot["items"][0]["detail_status"] != "ok"


def test_list_group_headline_survives_detail_and_does_not_replace_article_title(tmp_path):
    client = native.NativeSession(tmp_path)
    job = send(client, "api_begin", stream="news_all")
    row = {"id": "synthetic", "title": "개별 기사 제목", "source": "합성 매체"}
    grouped = {
        **row, "news_group_id": 7,
        "group_summary": {"headline": "묶음 대표 제목", "is_ai_headline": True, "member_count": 5},
    }
    base = response(job["request"]["url"], {"news_list": [grouped]})
    context = {"stream": "news_all", "job_id": job["job_id"], "response": base}
    child, = send(client, "api_prepare", **context)["requests"]
    detail = response(child["request"]["url"], {**row, "content": "개별 기사 본문"})
    assert send(client, "api_snapshot", **context, details=[detail])["ok"]
    path = tmp_path / "data/latest/news_all.json"
    item, = json.loads(path.read_text(encoding="utf-8"))["items"]
    assert (item.get("group_headline"), item["title"], item["body"]) == (
        "묶음 대표 제목", "개별 기사 제목", "개별 기사 본문",
    )
    stored = client.store.query(asof=datetime.now(UTC))
    assert stored[0]["item"]["group_headline"] == "묶음 대표 제목"


@pytest.mark.parametrize(("group_id", "summary"), [
    (False, {"headline": "제목"}), (0, {"headline": "제목"}), ("7", {"headline": "제목"}),
    (7, "제목"), (7, {"headline": {"title": "제목"}}), (7, {"headline": "  "}),
])
def test_invalid_group_headline_falls_back_to_individual_article(group_id, summary):
    item = jsonfeed.news_item({
        "id": "single", "title": "개별 제목", "news_group_id": group_id, "group_summary": summary,
    })
    assert item.title == "개별 제목" and item.group_headline is None


def test_repeated_title_with_new_publication_gets_its_own_detail(tmp_path):
    client = native.NativeSession(tmp_path)
    older = datetime.now(UTC) - timedelta(hours=2)
    newer = older + timedelta(hours=1)
    row = {"id": "event_1", "title": "합성 정기 속보", "created_at": older.isoformat()}
    old = jsonfeed.news_item({**row, "content": "이전 발표 본문"}, detail=True)
    client.store.save(Capture(stream="news_all", page_url="https://www.saveticker.com/news",
                              observed_at=older, items=[old]))
    job = send(client, "api_begin", stream="news_all")
    current = {**row, "id": "event_2", "created_at": newer.isoformat()}
    base = response(job["request"]["url"], {"news_list": [current]})
    context = {"stream": "news_all", "job_id": job["job_id"], "response": base}
    prepared = send(client, "api_prepare", **context)
    assert [r["source_id"] for r in prepared["requests"]] == ["event_2"]
    assert send(client, "api_snapshot", **context, details=[])["ok"]
    latest = json.loads((tmp_path / "data/latest/news_all.json").read_text(encoding="utf-8"))
    assert latest["items"][0]["body"] == ""
    assert datetime.fromisoformat(latest["items"][0]["published_at"]) == newer
    assert latest["items"][0]["id"] != old.id


def test_distinct_source_ids_keep_their_bodies_links_and_ledger_rows(tmp_path):
    from contextlib import closing

    from trader.features.ingest.saveticker_ingest import ingest_news
    from trader.kernel.ledger import db
    from trader.kernel.registry import discover
    from trader.kernel.registry.contracts import all_migrations

    client = native.NativeSession(tmp_path)
    first = {"id": "first", "title": "합성 속보", "source": "합성 매체",
             "created_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    second = {**first, "id": "second",
              "created_at": (datetime.now(UTC) - timedelta(minutes=20)).isoformat()}
    old = jsonfeed.news_item(first)
    client.store.save(Capture(stream="news_all", page_url="https://www.saveticker.com/news",
                              observed_at=datetime.now(UTC), items=[old]))
    job = send(client, "api_begin", stream="news_all")
    base = response(job["request"]["url"], {"news_list": [first, second]})
    context = {"stream": "news_all", "job_id": job["job_id"], "response": base}
    children = send(client, "api_prepare", **context)["requests"]
    assert len(children) == 2
    details = [response(child["request"]["url"], {**row, "content": body})
               for child, row, body in zip(children, (first, second), ("첫 본문", "둘째 본문"),
                                            strict=True)]
    del details[1]["data"]["created_at"]
    forged = {**details[1], "data": {**details[1]["data"], "source": "다른 매체"}}
    rejected = send(client, "api_snapshot", **context, details=[forged])
    assert rejected["error"] == "api_detail_unverified"
    assert send(client, "api_snapshot", **context, details=details)["ok"]
    path = tmp_path / "data/latest/news_all.json"
    one, two = json.loads(path.read_text(encoding="utf-8"))["items"]
    assert one["id"] == old.id and one["id"] != two["id"]
    assert (one["body"], two["body"]) == ("첫 본문", "둘째 본문")
    assert datetime.fromisoformat(two["published_at"]) == datetime.fromisoformat(
        second["created_at"]
    )

    client = native.NativeSession(tmp_path)
    job = send(client, "api_begin", stream="news_all")
    renamed = response(job["request"]["url"], {"news_list": [
        {**second, "title": "고쳐 쓴 둘째 제목"}, {**first, "title": "고쳐 쓴 첫 제목"}]})
    assert send(client, "api_snapshot", stream="news_all", job_id=job["job_id"],
                response=renamed)["ok"]
    renamed_items = json.loads(path.read_text(encoding="utf-8"))["items"]
    assert [(r["id"], r["body"]) for r in renamed_items] == [
        (two["id"], "둘째 본문"), (old.id, "첫 본문")]
    with closing(db.connect(tmp_path / "ledger.db")) as conn:
        db.init_schema(conn, all_migrations(discover()))
        ingest_news(conn, tmp_path)
        rows = conn.execute("SELECT url, summary FROM news_items WHERE market='KR'").fetchall()
    assert {tuple(row) for row in rows} == {
        ("https://www.saveticker.com/news#" + old.id, "첫 본문"),
        ("https://www.saveticker.com/news#" + two["id"], "둘째 본문"),
    }
