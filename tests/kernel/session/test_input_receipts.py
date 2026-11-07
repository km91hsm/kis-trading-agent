"""원문 없이 최종 컨텍스트와 일치하는 전달용 응답 기록을 남긴다."""

import hashlib
import json

import pytest


def test_receipt_describes_exact_context_without_copying_text(tmp_path):
    from trader.kernel.session import input_receipts

    context = {
        "news": {
            "data": {
                "stock": [
                    {
                        "id": 8,
                        "title": "합성 제목",
                        "body": "원문을 복제하지 않는다",
                        "url": "https://example.test/8",
                    }
                ]
            }
        },
        "truncation": {"truncated": True},
    }
    expected = hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert input_receipts.record(
        tmp_path,
        context,
        stage="researcher:r0:bull",
        provider=None,
        recorded_at="2026-11-04T01:00:00Z",
    )
    files = list((tmp_path / "input_receipts").glob("*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text())
    assert body["context_hash"] == expected
    assert body["provider"] is None
    assert body["groups"][0]["count"] == 1
    assert body["groups"][0]["refs"] == ["news:8", "https://example.test/8"]
    assert body["truncated"] is True
    assert "합성 제목" not in files[0].read_text()
    assert "원문을 복제" not in files[0].read_text()
    before = files[0].read_bytes()
    assert input_receipts.record(
        tmp_path / "other", {}, stage="trader", provider=None, recorded_at="2026-11-05T01:00:00Z"
    )
    assert files[0].read_bytes() == before


def test_receipt_failure_is_nonfatal_and_visible(tmp_path, caplog):
    from trader.kernel.session import input_receipts

    (tmp_path / "input_receipts").write_text("blocked")
    assert (
        input_receipts.record(
            tmp_path, {}, stage="trader", provider=None, recorded_at="2026-11-04T01:00:00Z"
        )
        is False
    )
    assert "입력 전달 기록 미기록: stage=trader" in caplog.text


def test_receipt_keeps_per_source_counts_without_source_text(tmp_path):
    from trader.kernel.session import input_receipts

    context = {
        "news": {
            "data": {
                "stock": [
                    {"id": 1, "source": "fixture-a", "title": "합성 제목 A"},
                    {"id": 2, "source": "fixture-b", "title": "합성 제목 B"},
                    {"id": 3, "source": "fixture-a", "title": "합성 제목 C"},
                ]
            }
        }
    }
    input_receipts.record(
        tmp_path, context, stage="trader", provider=None, recorded_at="2026-11-04T01:00:00Z"
    )
    receipt = json.loads(next((tmp_path / "input_receipts").glob("*.json")).read_text())
    assert receipt["groups"][0]["source_counts"] == {"fixture-a": 2, "fixture-b": 1}


def test_nested_section_failures_are_not_successful_empty_inputs():
    from copy import deepcopy

    from trader.kernel.session import input_receipts

    stamp = "2026-11-04T01:00:00Z"
    context = {
        "technical": {
            "quote": {
                "data": None,
                "error": "TimeoutError /private/path",
                "source": "fixture-q",
                "received_at": stamp,
            },
            "indicators": {
                "data": {"rsi": 50},
                "error": None,
                "source": "fixture-i",
                "received_at": stamp,
            },
        },
        "sentiment": {
            "community": {
                "data": None,
                "error": "TimeoutError",
                "source": "fixture-c",
                "received_at": stamp,
            },
        },
        "events": {"data": [], "error": None, "received_at": stamp},
    }
    before = deepcopy(context)
    canonical = input_receipts.public_context(context)
    assert input_receipts.public_context(canonical) == canonical
    groups = {group["id"]: group for group in input_receipts.describe(context)}
    assert (groups["technical"]["status"], groups["technical"]["count"]) == ("partial", 1)
    sections = {section["id"]: section for section in groups["technical"]["sections"]}
    assert sections["technical:quote"] == {
        "id": "technical:quote",
        "count": 0,
        "status": "error",
        "source": "fixture-q",
        "received_at": stamp,
    }
    assert sections["technical:indicators"]["status"] == "present"
    assert (groups["community"]["status"], groups["community"]["count"]) == ("error", 0)
    assert groups["community"]["received_at"] == stamp
    assert (groups["events"]["status"], groups["events"]["count"]) == ("empty", 0)
    assert input_receipts.describe(canonical) == input_receipts.describe(context)
    assert context == before
    assert "/private/path" not in json.dumps(groups)


def test_partial_community_retains_error_metadata_without_changing_source_counts(tmp_path):
    from trader.kernel.session import input_receipts

    stamp = "2026-11-04T01:00:00Z"
    context = {
        "sentiment": {
            "community": {
                "data": {
                    "posts": [
                        {
                            "source": "naver_blog/fixture",
                            "body": "합성 원문 비복제 A",
                            "url": "https://example.test/a",
                        },
                        {
                            "source": "fixture-community",
                            "body": "합성 원문 비복제 B",
                            "url": "https://example.test/b",
                        },
                    ]
                },
                "error": "TimeoutError /private/path",
                "received_at": stamp,
            }
        }
    }
    canonical = input_receipts.public_context(context)
    assert input_receipts.public_context(canonical) == canonical
    assert input_receipts.record(
        tmp_path, context, stage="trader", provider=None, recorded_at=stamp
    )
    raw = next((tmp_path / "input_receipts").glob("*.json")).read_text()
    groups = {group["id"]: group for group in json.loads(raw)["groups"]}
    assert groups["blog_posts"]["count"] == groups["community"]["count"] == 1
    assert groups["blog_posts"]["source_counts"] == {"naver_blog/fixture": 1}
    assert groups["community"]["source_counts"] == {"fixture-community": 1}
    assert groups["blog_posts"]["sections"][0]["status"] == "partial"
    assert groups["community"]["sections"][0]["status"] == "partial"
    assert "합성 원문" not in raw and "/private/path" not in raw
    empty = {
        "sentiment": {"community": {"data": {"posts": []}, "error": None, "received_at": stamp}}
    }
    group = input_receipts.describe(empty)[0]
    assert group["id"] == "community" and group["status"] == "empty" and group["count"] == 0


def test_community_section_metadata_preserves_existing_item_ids_and_counts():
    from trader.kernel.session import input_receipts

    stamp = "2026-11-04T01:00:00Z"
    for error in (None, "TimeoutError"):
        context = {
            "sentiment": {
                "community": {
                    "data": {
                        "posts": [
                            {
                                "source": "fixture-community",
                                "title": "합성 제목 A",
                                "url": "https://example.test/a",
                            },
                            {
                                "source": "fixture-community",
                                "title": "합성 제목 B",
                                "url": "https://example.test/b",
                            },
                        ]
                    },
                    "error": error,
                    "received_at": stamp,
                }
            }
        }
        canonical = input_receipts.public_context(context)
        assert isinstance(canonical["community"]["data"], list)
        assert input_receipts.public_context(canonical) == canonical
        first = input_receipts.describe(context)
        assert input_receipts.describe(canonical) == first
        group = next(row for row in first if row["id"] == "community")
        assert [item["id"] for item in group["items"]] == ["community:0", "community:1"]
        assert group["count"] == group["listed_count"] == 2
        assert group["status"] == ("partial" if error else "present")
        assert [item["source"] for item in group["items"]] == [
            "fixture-community",
            "fixture-community",
        ]
        assert [item["refs"] for item in group["items"]] == [
            ["https://example.test/a"],
            ["https://example.test/b"],
        ]


@pytest.mark.parametrize(
    "reason",
    [
        "disabled",
        "phase6",
        "phase7",
        "phase8",
        "no saveticker dir",
        "not available for US",
        "no manual input",
        "no sector peers",
    ],
)
def test_intentional_skip_is_not_an_input_error(reason):
    from trader.kernel.session import input_receipts

    context = {
        "sentiment": {
            "manual_inputs": {
                "data": None,
                "received_at": "2026-11-04T01:00:00Z",
                "error": reason,
            }
        }
    }
    groups = input_receipts.describe(context)
    assert groups[0]["status"] == "skipped"
    assert groups[0]["count"] == 0
    assert groups[0]["sections"][0]["status"] == "skipped"
    assert context["sentiment"]["manual_inputs"]["error"] == reason


@pytest.mark.parametrize(
    ("data", "error", "expected", "count"),
    [
        ([{"foreign": 10}], None, "present", 1),
        ([], None, "empty", 0),
        (None, "TimeoutError", "error", 0),
        ([{"foreign": 10}], "TimeoutError", "partial", 1),
    ],
)
def test_skip_siblings_do_not_create_false_partial_status(data, error, expected, count):
    from trader.kernel.session import input_receipts

    context = {
        "sentiment": {
            "short_interest": {"data": None, "error": "phase6"},
            "trends": {"data": None, "error": "phase8"},
            "investor_flow": {"data": data, "error": error},
        }
    }
    group = input_receipts.describe(context)[0]
    assert group["status"] == expected and group["count"] == count
    sections = {section["id"]: section for section in group["sections"]}
    assert sections["sentiment:short_interest"]["status"] == "skipped"
    assert sections["sentiment:trends"]["status"] == "skipped"
    assert sections["sentiment:investor_flow"]["status"] == expected


def test_all_skipped_sections_remain_skipped_in_receipt(tmp_path):
    from trader.kernel.session import input_receipts

    context = {
        "sentiment": {
            "short_interest": {"data": None, "error": "phase6"},
            "trends": {"data": None, "error": "phase8"},
            "community": {"data": None, "error": "disabled"},
        }
    }
    canonical = input_receipts.public_context(context)
    assert input_receipts.public_context(canonical) == canonical
    assert all(group["status"] == "skipped" for group in input_receipts.describe(context))
    input_receipts.record(
        tmp_path, context, stage="trader", provider=None, recorded_at="2026-11-04T01:00:00Z"
    )
    receipt = json.loads(next((tmp_path / "input_receipts").glob("*.json")).read_text())
    assert all(group["source_counts"] == {} and group["count"] == 0 for group in receipt["groups"])
    assert all(
        section["status"] == "skipped"
        for group in receipt["groups"]
        for section in group["sections"]
    )


@pytest.mark.parametrize(
    ("items", "expected"),
    [([], "error"), ([{"source": "합성 매체", "title": "합성 기사"}], "partial")],
)
def test_collector_inner_failure_is_not_hidden_by_successful_outer_section(
    tmp_path, items, expected
):
    from copy import deepcopy

    from trader.kernel.session import input_receipts

    context = {
        "news": {
            "data": {
                "stock": items,
                "macro": [],
                "sources": {"google_rss": "ConnectError: /private/account/path", "naver": "ok"},
            },
            "error": None,
            "received_at": "2026-11-04T01:00:00Z",
        }
    }
    before = deepcopy(context)
    digest = hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    group = input_receipts.describe(context)[0]
    assert group["status"] == expected and group["count"] == len(items)
    assert group["sections"][0]["source_outcomes"] == [
        {"source": "google_rss", "status": "error", "count": None},
        {"source": "naver", "status": "success", "count": None},
    ]
    input_receipts.record(
        tmp_path, context, stage="trader", provider=None, recorded_at="2026-11-04T01:00:00Z"
    )
    raw = next((tmp_path / "input_receipts").glob("*.json")).read_text()
    receipt = json.loads(raw)
    assert receipt["context_hash"] == digest
    assert receipt["groups"][0]["source_counts"] == ({"합성 매체": 1} if items else {})
    assert (
        receipt["groups"][0]["sections"][0]["source_outcomes"]
        == group["sections"][0]["source_outcomes"]
    )
    assert "/private/account/path" not in raw and "합성 기사" not in raw
    assert context == before


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        ({"google_rss": "ok", "naver": "no api key"}, "empty"),
        ({"reddit": "not available for KR"}, "skipped"),
        ({"reddit": "not available for US", "clien": "disabled"}, "skipped"),
        ({"store": "0건 합침"}, "empty"),
    ],
)
def test_inner_source_success_and_explicit_skip_do_not_become_errors(sources, expected):
    from trader.kernel.session import input_receipts

    group = input_receipts.describe(
        {"news": {"data": {"stock": [], "sources": sources}, "error": None}}
    )[0]
    assert group["status"] == expected and group["count"] == 0
    outcomes = group["sections"][0]["source_outcomes"]
    assert all(row["status"] in {"success", "skipped"} and row["count"] is None for row in outcomes)


@pytest.mark.parametrize("failed_blog", [False, True])
def test_community_split_keeps_each_source_failure_without_changing_ids(failed_blog):
    from copy import deepcopy

    from trader.kernel.session import input_receipts

    source = "clien" if failed_blog else "naver_blog/fixture"
    context = {
        "sentiment": {
            "community": {
                "data": {
                    "posts": [
                        {"source": source, "body": "합성 본문", "url": "https://example.test/post"}
                    ],
                    "sources": {
                        "clien": "ok" if failed_blog else "ConnectError: /private/path",
                        "naver_blog/fixture": "ConnectError: /private/path"
                        if failed_blog
                        else "ok",
                        "reddit": "not available for KR",
                    },
                },
                "error": None,
                "received_at": "2026-11-04T01:00:00Z",
            }
        }
    }
    before = deepcopy(context)
    canonical = input_receipts.public_context(context)
    assert input_receipts.public_context(canonical) == canonical
    groups = {row["id"]: row for row in input_receipts.describe(context)}
    present, failed = ("community", "blog_posts") if failed_blog else ("blog_posts", "community")
    assert (groups[present]["status"], groups[present]["count"]) == ("present", 1)
    assert (groups[failed]["status"], groups[failed]["count"]) == ("error", 0)
    assert groups[present]["items"][0]["id"] == f"{present}:0"
    assert groups["blog_posts"]["sections"][0]["source_outcomes"] == [
        {
            "source": "naver_blog/fixture",
            "status": "error" if failed_blog else "success",
            "count": None,
        },
    ]
    assert "/private/path" not in json.dumps(groups)
    assert context == before
