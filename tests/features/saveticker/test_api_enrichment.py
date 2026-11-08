import json

import pytest

from trader.features.saveticker import api_contract, native


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
