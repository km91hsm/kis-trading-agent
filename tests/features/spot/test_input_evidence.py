"""합성 입력으로 과거 스냅샷/전달용 응답 기록/모델 인용의 경계를 검사한다."""

import json
from datetime import date

import pytest

from tests.features.conftest import web_client
from tests.features.spot.helpers import ok_decision, ok_report
from trader.features.spot.feature import FEATURE
from trader.features.spot.ledger import ingest_checkpoint
from trader.kernel.execution.ledger import decision_id
from trader.kernel.ledger import db
from trader.kernel.session import run_store

DAY = date(2026, 11, 4)
ASOF = "2026-11-04T01:00:00Z"


@pytest.fixture
def case(project):
    client = web_client(project, [FEATURE])
    with db.connect(project / "state/trader.db") as conn:
        ingest_checkpoint(conn, DAY, "KR", "005930", "30_decision", {"data": ok_decision()})
        did = decision_id(conn, DAY, "KR", "005930")
    return client, did, project / "state/runs/2026-11-04/KR/005930"


def snapshot():
    return {
        "meta": {"ticker": "005930", "market": "KR", "asof": ASOF},
        "news_macro": {
            "news": {
                "data": {
                    "stock": [
                        {
                            "id": 17,
                            "title": "합성 뉴스",
                            "source": "fixture",
                            "url": "https://example.test/17",
                        }
                    ]
                },
                "received_at": ASOF,
                "error": None,
            },
            "macro": {
                "us10y": {"data": {"value": 4.2}, "source": "fixture", "source_asof": "2026-11-03"}
            },
            "events": {"data": None, "received_at": ASOF, "error": "private /secret/path"},
        },
        "sentiment": {
            "community": {
                "data": {
                    "posts": [
                        {
                            "title": f"합성 블로그 {i}",
                            "url": f"https://example.test/blog/{i}",
                            "source": "naver_blog/fixture",
                            "body": "가" * 1800,
                            "posted_at": ASOF,
                            "received_at": ASOF,
                        }
                        for i in range(5)
                    ]
                }
            }
        },
        "accounts": [{"secret": "DO_NOT_EXPOSE"}],
    }


def test_missing_snapshot_is_unrecorded_and_get_creates_no_directory(case):
    client, did, rd = case
    body = client.get(f"/api/decisions/spot/{did}/inputs")
    assert body.status_code == 200
    assert body.json()["snapshot"]["status"] == "missing"
    assert body.json()["delivery"]["status"] == "unrecorded"
    assert not rd.exists()
    assert client.get("/api/decisions/spot/9999/inputs").status_code == 404


def test_snapshot_and_current_reconstruction_do_not_claim_actual_delivery(case, project):
    client, did, rd = case
    rd.mkdir(parents=True)
    run_store.write_checkpoint(rd, "00_inputs", snapshot())
    before = (rd / "00_inputs.json").read_bytes()
    with db.connect(project / "state/trader.db") as conn:
        ingest_checkpoint(
            conn,
            DAY,
            "KR",
            "005930",
            "10_analyst_news_macro",
            {
                "data": ok_report(
                    role="news_macro", evidence=[{"source_id": "news:17", "claim": "합성 근거"}]
                )
            },
        )
    body = client.get(f"/api/decisions/spot/{did}/inputs").json()
    assert body["snapshot"]["status"] == "present"
    assert body["snapshot"]["asof"] == ASOF
    assert body["delivery"]["status"] == "unrecorded"
    assert body["reconstruction"]["status"] == "current_rules"
    groups = {g["id"]: g for g in body["groups"]}
    assert groups["blog_posts"]["stored_count"] == 5
    assert groups["blog_posts"]["reconstructed_count"] == 5
    assert groups["events"]["status"] == "error"
    assert groups["news"]["received_at"] == ASOF
    item = next(i for i in body["items"] if "news:17" in i["refs"])
    assert item["title"] == "합성 뉴스"
    assert item["url"] == "https://example.test/17"
    assert item["cited"] is True
    assert all(not i["cited"] for i in body["items"] if i["group"] == "blog_posts")
    assert (rd / "00_inputs.json").read_bytes() == before
    assert "DO_NOT_EXPOSE" not in json.dumps(body)
    assert "private /secret/path" not in json.dumps(body)
    assert "가" * 100 not in json.dumps(body, ensure_ascii=False)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "//example.test/x",
        "https://user:secret@example.test/x",
    ],
)
def test_unsafe_original_url_is_not_exposed(case, url):
    client, did, rd = case
    rd.mkdir(parents=True)
    data = snapshot()
    data["news_macro"]["news"]["data"]["stock"][0]["url"] = url
    run_store.write_checkpoint(rd, "00_inputs", data)
    body = client.get(f"/api/decisions/spot/{did}/inputs").json()
    item = next(i for i in body["items"] if "news:17" in i["refs"])
    assert item["url"] is None
    assert url not in json.dumps(body)


def test_corrupt_and_oversized_snapshots_are_visible_without_raw_content(case):
    client, did, rd = case
    rd.mkdir(parents=True)
    path = rd / "00_inputs.json"
    path.write_text("{ broken private value")
    assert (
        client.get(f"/api/decisions/spot/{did}/inputs").json()["snapshot"]["status"] == "unreadable"
    )
    path.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    assert (
        client.get(f"/api/decisions/spot/{did}/inputs").json()["snapshot"]["status"] == "too_large"
    )


def test_receipt_view_exposes_only_safe_metadata_and_keeps_unreadable_count(case):
    from trader.features.spot.input_evidence import public_context
    from trader.kernel.session import input_receipts

    client, did, rd = case
    rd.mkdir(parents=True)
    run_store.write_checkpoint(rd, "00_inputs", snapshot())
    assert input_receipts.record(
        rd, public_context(snapshot()), stage="researcher:r0:bull", provider=None, recorded_at=ASOF
    )
    path = next((rd / "input_receipts").glob("*.json"))
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["private_payload"] = "DO_NOT_EXPOSE"
    receipt["groups"][0]["refs"].append("javascript:private_secret")
    path.write_text(json.dumps(receipt))
    (rd / "input_receipts" / "broken.json").write_text("{broken")
    body = client.get(f"/api/decisions/spot/{did}/inputs").json()
    assert body["delivery"]["status"] == "recorded"
    assert body["delivery"]["unreadable_count"] == 1
    assert body["delivery"]["receipts"][0]["groups"][0]["refs"] == [
        "news:17",
        "https://example.test/17",
    ]
    assert "DO_NOT_EXPOSE" not in json.dumps(body)
    assert "private_secret" not in json.dumps(body)


@pytest.mark.parametrize(
    "data",
    [
        {"meta": "bad", "sentiment": {"community": {"data": {"posts": None}}}},
        {"meta": [], "news_macro": []},
    ],
)
def test_malformed_saved_shapes_return_readable_unavailable_evidence(case, data):
    client, did, rd = case
    rd.mkdir(parents=True)
    run_store.write_checkpoint(rd, "00_inputs", data)
    response = client.get(f"/api/decisions/spot/{did}/inputs")
    assert response.status_code == 200
    assert response.json()["snapshot"]["asof"] is None
    assert response.json()["groups"] == []


def test_partial_source_coverage_preserves_missing_zero_and_observation_times(case):
    client, did, rd = case
    rd.mkdir(parents=True)
    data = snapshot()
    data["source_coverage"] = {
        "macro": {
            "us10y": {
                "status": "present",
                "source": "fixture",
                "source_asof": "2026-11-03",
                "received_at": ASOF,
            },
            "dxy": {"status": "missing", "source": "fixture", "data_gaps": ["omitted"]},
        },
        "naver_blog/empty": {"status": "missing", "items": 0, "source": "fixture"},
    }
    run_store.write_checkpoint(rd, "00_inputs", data)
    body = client.get(f"/api/decisions/spot/{did}/inputs").json()
    quality = {row["id"]: row for row in body["source_coverage"]}
    assert quality["macro:us10y"]["source_asof"] == "2026-11-03"
    assert quality["macro:dxy"]["status"] == "missing"
    assert quality["macro:dxy"]["items"] is None
    assert quality["naver_blog/empty"]["items"] == 0
    assert quality["macro:dxy"]["gaps_count"] == 1


def test_receipt_references_are_bound_to_saved_items_but_not_model_citations(case):
    from trader.features.spot.input_evidence import public_context
    from trader.kernel.session import input_receipts

    client, did, rd = case
    rd.mkdir(parents=True)
    data = snapshot()
    run_store.write_checkpoint(rd, "00_inputs", data)
    input_receipts.record(rd, public_context(data), stage="trader", provider=None, recorded_at=ASOF)
    body = client.get(f"/api/decisions/spot/{did}/inputs").json()
    assert body["delivery"]["status"] == "recorded"
    assert all(not item["cited"] for item in body["items"])
    assert any("news:17" in group["refs"] for group in body["delivery"]["receipts"][0]["groups"])


def test_section_citation_links_to_input_group_without_claiming_each_article_was_cited(
    case, project
):
    client, did, rd = case
    rd.mkdir(parents=True)
    data = snapshot()
    data["sentiment"]["investor_flow"] = {
        "data": [{"date": "2026-11-03", "foreign": 10}],
        "received_at": ASOF,
        "error": None,
    }
    run_store.write_checkpoint(rd, "00_inputs", data)
    with db.connect(project / "state/trader.db") as conn:
        ingest_checkpoint(
            conn,
            DAY,
            "KR",
            "005930",
            "10_analyst_news_macro",
            {
                "data": ok_report(
                    role="news_macro",
                    evidence=[{"source_id": "news_macro.news", "claim": "묶음 근거"}],
                )
            },
        )
    body = client.get(f"/api/decisions/spot/{did}/inputs").json()
    group = next(row for row in body["groups"] if row["id"] == "news")
    assert "news_macro.news" in group["refs"]
    assert group["cited"] is True
    assert all(not item["cited"] for item in body["items"] if item["group"] == "news")
    assert any("sentiment.investor_flow" in group["refs"] for group in body["groups"])


def test_reconstruction_reports_dropped_root_as_zero_not_unrecorded(case):
    client, did, rd = case
    rd.mkdir(parents=True)
    data = snapshot()
    for post in data["sentiment"]["community"]["data"]["posts"]:
        post["url"] = "https://example.test/" + "x" * 20000
    run_store.write_checkpoint(rd, "00_inputs", data)
    body = client.get(f"/api/decisions/spot/{did}/inputs").json()
    blogs = next(row for row in body["groups"] if row["id"] == "blog_posts")
    assert blogs["stored_count"] == 5
    assert blogs["reconstructed_count"] == 0
    assert blogs["omitted_count"] == 5


def test_nested_collection_failures_remain_visible_in_saved_input_and_receipt(case):
    from trader.kernel.session import input_receipts

    client, did, rd = case
    rd.mkdir(parents=True)
    data = {
        "meta": {"asof": ASOF},
        "technical": {
            "quote": {
                "data": None,
                "error": "TimeoutError /private/path",
                "source": "fixture-q",
                "received_at": ASOF,
            }
        },
        "sentiment": {
            "community": {
                "data": None,
                "error": "TimeoutError",
                "source": "fixture-c",
                "received_at": ASOF,
            }
        },
    }
    run_store.write_checkpoint(rd, "00_inputs", data)
    input_receipts.record(rd, data, stage="checkpoint:00_inputs", provider=None, recorded_at=ASOF)
    body = client.get(f"/api/decisions/spot/{did}/inputs").json()
    groups = {row["id"]: row for row in body["groups"]}
    assert groups["technical"]["stored_count"] == 0
    assert groups["technical"]["status"] == "error"
    assert groups["technical"]["sections"][0]["id"] == "technical:quote"
    assert groups["community"]["status"] == "error"
    assert groups["community"]["received_at"] == ASOF
    receipt_groups = {row["id"]: row for row in body["delivery"]["receipts"][0]["groups"]}
    assert receipt_groups["technical"]["sections"][0]["status"] == "error"
    assert receipt_groups["technical"]["source_counts"] == {}
    assert "/private/path" not in json.dumps(body)


def test_intentional_skip_survives_input_api_and_receipt_allowlist(case):
    from trader.kernel.session import input_receipts

    client, did, rd = case
    rd.mkdir(parents=True)
    data = {
        "meta": {"asof": ASOF},
        "sentiment": {
            "short_interest": {"data": None, "error": "phase6"},
            "trends": {"data": None, "error": "phase8"},
        },
    }
    run_store.write_checkpoint(rd, "00_inputs", data)
    before = (rd / "00_inputs.json").read_bytes()
    input_receipts.record(rd, data, stage="checkpoint:00_inputs", provider=None, recorded_at=ASOF)
    body = client.get(f"/api/decisions/spot/{did}/inputs").json()
    group = body["groups"][0]
    assert group["status"] == "skipped" and group["stored_count"] == 0
    assert all(section["status"] == "skipped" for section in group["sections"])
    receipt = body["delivery"]["receipts"][0]["groups"][0]
    assert all(section["status"] == "skipped" for section in receipt["sections"])
    assert (rd / "00_inputs.json").read_bytes() == before


def test_inner_source_outcomes_are_sanitized_and_preserved_through_receipt_api(case):
    from trader.kernel.session import input_receipts

    client, did, rd = case
    rd.mkdir(parents=True)
    data = {
        "news_macro": {
            "news": {
                "data": {
                    "stock": [],
                    "sources": {
                        "google_rss": "ConnectError /private/account/path",
                        "naver": "no api key",
                    },
                },
                "error": None,
                "received_at": ASOF,
            }
        }
    }
    run_store.write_checkpoint(rd, "00_inputs", data)
    input_receipts.record(rd, data, stage="checkpoint:00_inputs", provider=None, recorded_at=ASOF)
    path = next((rd / "input_receipts").glob("*.json"))
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["groups"][0]["sections"][0]["source_outcomes"] = [
        {"source": "google_rss", "status": "error", "count": None, "error": "SECRET_PATH"},
        {"source": "naver", "status": "skipped", "count": None},
    ]
    path.write_text(json.dumps(receipt))
    body = client.get(f"/api/decisions/spot/{did}/inputs").json()
    group = body["groups"][0]
    assert group["status"] == "error" and group["stored_count"] == 0
    expected = [
        {"source": "google_rss", "status": "error", "count": None},
        {"source": "naver", "status": "skipped", "count": None},
    ]
    assert group["sections"][0]["source_outcomes"] == expected
    assert (
        body["delivery"]["receipts"][0]["groups"][0]["sections"][0]["source_outcomes"] == expected
    )
    assert "SECRET_PATH" not in json.dumps(body) and "/private/account/path" not in json.dumps(body)
