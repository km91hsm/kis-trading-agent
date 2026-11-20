"""합성 뉴스 목록으로 정상 페이지 연결과 중단 후 재시도를 확인한다."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from trader.features.saveticker import api_jobs, native


def call(client, kind, **fields):
    return client.handle(dict(id=1, type=kind, stream="news_all", **fields))


def rows(start, size=20):
    return [{"id": str(n), "title": f"합성 기사 {n}"} for n in range(start, start - size, -1)]


def page(client, job, number, articles, **overrides):
    url = job["request"]["url"]
    assert f"page={number}&page_size=20&sort=created_at_desc" in url
    data = {"page": number, "page_size": 20, "total_count": 200, "news_list": articles}
    return call(
        client,
        "api_snapshot" if number == 1 else "api_news_page",
        job_id=job["job_id"],
        news_pages=True,
        response={
            "url": url,
            "status": 200,
            "content_type": "application/json",
            "data": {**data, **overrides},
        },
    )


def head(client, articles):
    return page(client, call(client, "api_begin"), 1, articles)


def test_gap_continues_after_restart_and_preserves_front_page(tmp_path):
    client = native.NativeSession(tmp_path)
    assert not head(client, rows(20)).get("next_page")
    assert not head(client, rows(30)).get("next_page")
    current = rows(100)
    saved = head(client, current)
    assert saved["ok"] and saved["count"] == 20
    second = saved.get("next_page")
    assert second, "직전 목록과 연결되지 않은 채 20건만 저장했다"
    # 첫 쪽 끝의 기사가 새 기사 유입으로 중복된다.
    third = page(client, second, 2, rows(81))["next_page"]
    assert not page(client, third, 3, rows(61)).get("next_page"), "한 주기에는 두 쪽만 추가"
    client = native.NativeSession(tmp_path)
    boundary = head(client, current)["next_page"]
    fourth = page(client, boundary, 3, rows(61))["next_page"]
    assert not page(client, fourth, 4, rows(41)).get("next_page"), "기존 기사와 연결되면 종료"
    assert not head(client, current).get("next_page")
    snapshot = json.loads((tmp_path / "data/latest/news_all.json").read_text(encoding="utf-8"))
    assert [i["title"] for i in snapshot["items"]] == [i["title"] for i in current]
    path = next((tmp_path / "data").glob("*/news.jsonl"))
    lines = path.read_text(encoding="utf-8").splitlines()
    items = [json.loads(line)["item"]["title"] for line in lines]
    assert len(items) == len(set(items)) == 100


def test_page_rejection_preserves_latest_and_retries_same_page_after_cooldown(
    tmp_path, monkeypatch
):
    stamp = datetime.now(UTC)
    monkeypatch.setattr(api_jobs, "now", lambda: stamp)
    client = native.NativeSession(tmp_path)
    head(client, rows(20))
    current = rows(55)
    child = head(client, current).get("next_page")
    assert child
    bad = page(client, child, 2, rows(35), page=1)
    assert bad["error"] == "api_shape_unverified"
    client = native.NativeSession(tmp_path)
    assert not head(client, current).get("next_page")
    stamp += timedelta(minutes=6)
    child = head(client, current)["next_page"]
    assert page(client, child, 2, rows(35))["ok"]
    assert not head(client, current).get("next_page")


def test_new_gap_while_backfilling_starts_from_new_head(tmp_path):
    client = native.NativeSession(tmp_path)
    head(client, rows(20))
    second = head(client, rows(100)).get("next_page")
    assert second
    third = page(client, second, 2, rows(80))["next_page"]
    page(client, third, 3, rows(60))
    second = head(client, rows(140))["next_page"]
    assert page(client, second, 2, rows(120))["next_page"]


@pytest.mark.parametrize("number", [0, 101, True])
def test_descriptor_refuses_out_of_bounds_news_pages(number):
    from trader.features.saveticker import api_contract

    assert api_contract.descriptor({"stream": "news_all"}, news_page=number) is None


@pytest.mark.parametrize("field", ["page", "page_size", "total_count"])
def test_additional_page_requires_observed_pagination_metadata(tmp_path, field):
    client = native.NativeSession(tmp_path)
    head(client, rows(20))
    child = head(client, rows(60))["next_page"]
    data = {"page": 2, "page_size": 20, "total_count": 200, "news_list": rows(40)}
    del data[field]
    result = call(
        client,
        "api_news_page",
        job_id=child["job_id"],
        response={
            "url": child["request"]["url"],
            "status": 200,
            "content_type": "application/json",
            "data": data,
        },
    )
    assert result.get("error") == "api_shape_unverified"


def test_truncated_head_keeps_valid_latest_and_existing_gap_cursor(tmp_path):
    client = native.NativeSession(tmp_path)
    head(client, rows(20))
    second = head(client, rows(100))["next_page"]
    third = page(client, second, 2, rows(80))["next_page"]
    assert page(client, third, 3, rows(60))["ok"]
    job = call(client, "api_begin")
    result = page(client, job, 1, rows(106, 19))
    assert result.get("error") == "api_shape_unverified"
    latest = json.loads((tmp_path / "data/latest/news_all.json").read_text(encoding="utf-8"))
    assert latest["items"][0]["title"] == "합성 기사 100"
    assert call(client, "api_cancel", job_id=job["job_id"])["ok"]
    boundary = head(client, rows(102))["next_page"]
    fourth = page(client, boundary, 3, rows(62))["next_page"]
    assert page(client, fourth, 4, rows(42))["ok"]


def test_repeated_title_does_not_bridge_distinct_server_news_ids(tmp_path):
    client = native.NativeSession(tmp_path)
    first, new, middle = rows(20), rows(100), rows(80)
    first[0]["title"] = new[0]["title"] = middle[0]["title"] = "매시 종합"
    assert head(client, first)["ok"]
    child = head(client, new).get("next_page")
    assert child, "제목과 매체가 일치해도 서버의 기사 ID는 다르다"
    assert page(client, child, 2, middle).get("next_page")


def test_backfill_adds_newly_observed_group_headline_to_known_article(tmp_path):
    client = native.NativeSession(tmp_path)
    original = rows(20)
    original[0]["is_headline_only"] = True
    assert head(client, original)["ok"]
    child = head(client, rows(100))["next_page"]
    past = rows(39)
    past[-1].update(news_group_id=7, group_summary={"headline": "묶음 대표 제목"})
    assert page(client, child, 2, past)["ok"]
    path = next((tmp_path / "data").glob("*/news.jsonl"))
    revisions = [json.loads(line)["item"] for line in path.read_text(encoding="utf-8").splitlines()]
    current = [r for r in revisions if r["title"] == "합성 기사 20"][-1]
    assert current.get("group_headline") == "묶음 대표 제목"
    assert current["detail_status"] == "ok"


def test_backfill_records_group_removal_once_at_observation_without_losing_body(
    tmp_path, monkeypatch
):
    from trader.features.saveticker import jsonfeed
    from trader.features.saveticker.models import Capture

    stamp = datetime(2026, 11, 20, tzinfo=UTC)
    monkeypatch.setattr(api_jobs, "now", lambda: stamp)
    client = native.NativeSession(tmp_path)
    row = rows(20, size=1)[0]
    item = jsonfeed.news_item({**row, "content": "개별 기사 본문"}, detail=True)
    item = item.model_copy(update={"group_headline": "예전 묶음 제목"})
    client.store.save(Capture(stream="news_all", page_url="https://www.saveticker.com/news",
                              observed_at=stamp, items=[item]))
    for next_head in (100, 140):
        stamp += timedelta(minutes=10)
        child = head(client, rows(next_head))["next_page"]
        assert page(client, child, 2, rows(39))["ok"]

    path = next((tmp_path / "data").glob("*/news.jsonl"))
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    history = [r for r in records if r["item"]["id"] == item.id]
    assert [(r["item"].get("group_headline"), r["item"]["body"]) for r in history] == [
        ("예전 묶음 제목", "개별 기사 본문"), (None, "개별 기사 본문"),
    ]
    removed_at = stamp - timedelta(minutes=10)
    assert datetime.fromisoformat(history[1]["received_at"]) == removed_at
    observations = ((removed_at - timedelta(seconds=1), "예전 묶음 제목"), (removed_at, None))
    for asof, expected in observations:
        observed = next(
            r["item"] for r in client.store.query(asof=asof) if r["item"]["id"] == item.id
        )
        assert observed.get("group_headline") == expected


def test_legacy_news_head_uses_its_title_identity_for_first_gap(tmp_path):
    client = native.NativeSession(tmp_path)
    job = call(client, "api_begin")
    result = call(
        client,
        "api_snapshot",
        job_id=job["job_id"],
        response={
            "url": job["request"]["url"],
            "status": 200,
            "content_type": "application/json",
            "data": {"news_list": rows(20)},
        },
    )
    assert result["ok"]
    second = head(client, rows(50)).get("next_page")
    assert second
    ack = page(client, second, 2, rows(30))
    assert ack["ok"] and not ack.get("next_page")


def test_boundary_is_rechecked_when_deletion_shifts_next_unseen_item(tmp_path):
    client = native.NativeSession(tmp_path)
    head(client, rows(20))
    second = head(client, rows(100))["next_page"]
    third = page(client, second, 2, rows(80))["next_page"]
    assert page(client, third, 3, rows(60))["ok"]
    # 서버에서 65번이 사라지면 아직 받지 않은 40번이 4쪽에서 3쪽 끝으로 이동한다.
    boundary = head(client, rows(100))["next_page"]
    fourth = page(client, boundary, 3, rows(59), total_count=199)["next_page"]
    assert page(client, fourth, 4, rows(39), total_count=199)["ok"]
    path = next((tmp_path / "data").glob("*/news.jsonl"))
    lines = path.read_text(encoding="utf-8").splitlines()
    titles = {json.loads(line)["item"]["title"] for line in lines}
    assert "합성 기사 40" in titles
