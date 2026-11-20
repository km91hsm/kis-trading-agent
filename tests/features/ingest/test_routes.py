"""ingest 웹 자원: `/news`(저장소 행 그대로, 토큰 없음)·`/news/events`(공시)·`/news/manual`(토큰).

옛 `tests/web/test_news_api.py` 1건·`test_manual_inputs_api.py` 2건 이식 + tickers 배열 유지.
fixture 는 구조만 본뜬 합성 행이다(실제 기사 제목 아님)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.features.conftest import AUTH, web_client
from trader.features.ingest import manual_inputs, news_store, saveticker_ingest
from trader.features.ingest.feature import FEATURE
from trader.features.saveticker.dom import identity
from trader.features.saveticker.models import Capture, Item
from trader.features.saveticker.store import Store
from trader.kernel.ledger import db

NOW = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)


def _client(project):
    return web_client(project, [FEATURE], now=NOW)


def _seed(project):
    conn = db.connect(project / "state" / "trader.db")
    try:
        news_store.insert_items(
            conn,
            [
                {"title": "가나전자 합성 기사 1", "url": "https://example.test/1",
                 "published_at": (NOW - timedelta(hours=1)).isoformat(),
                 "received_at": (NOW - timedelta(hours=1)).isoformat(), "summary": "s"},
                {"title": "미래 합성 기사", "url": "https://example.test/2",
                 "published_at": (NOW + timedelta(hours=1)).isoformat(),
                 "received_at": (NOW + timedelta(hours=1)).isoformat()},
            ],
            market="KR", source="google_rss", query="합성질의", lang="ko",
            names={"005930": "가나전자"},
        )  # fmt: skip
        news_store.insert_items(
            conn,
            [{"title": "합성 공시 제목", "url": "https://example.test/d1", "severity": "critical",
              "tickers": ["005930"], "published_at": (NOW - timedelta(hours=2)).isoformat(),
              "received_at": (NOW - timedelta(hours=2)).isoformat()}],
            market="KR", source="dart", query="disclosure", lang="ko", names={},
            extra_tickers=True,
        )  # fmt: skip
    finally:
        conn.close()


def test_api_news_lists_recent_items_with_tags(project):
    c = _client(project)
    _seed(project)
    r = c.get("/api/news?market=KR")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3 and body["hours"] == 48 and body["asof"] == NOW.isoformat()
    titles = [i["title"] for i in body["items"]]
    assert titles == ["가나전자 합성 기사 1", "합성 공시 제목"]  # 미래 항목은 보이지 않는다
    assert c.get("/api/news?market=KR&ticker=000660").json()["items"] == []
    assert c.get("/api/news?market=XX").status_code == 400
    assert c.get("/api/news?market=US").json()["items"] == []
    assert c.get("/api/news?market=KR&hours=0").status_code == 422
    assert c.get("/api/news?market=KR&limit=501").status_code == 422


def test_news_rows_keep_tickers_and_store_fields(project):
    c = _client(project)
    _seed(project)
    item = c.get("/api/news?market=KR&limit=1").json()["items"][0]
    assert item["tickers"] == ["005930"] and "url_hash" not in item and "tickers_json" not in item
    assert {"id", "source", "query", "title", "url", "published_at", "received_at"} <= set(item)
    assert "is_catalyst" not in item  # 라벨은 scan `/news/labels` 가 낸다


@pytest.mark.parametrize("fast", [False, True])
def test_news_list_uses_observed_group_headlines_without_changing_article(project, fast):
    origin = NOW - timedelta(minutes=4)
    clock = [origin]
    client = web_client(project, [FEATURE], now=lambda: clock[0])
    root = project / "state" / "external" / "saveticker"
    store = Store(root / "data")
    title = "합성 개별 속보"
    issuer = "합성 통신"
    item = Item(
        id="legacy-not-hex" if fast else identity("news", issuer, title),
        source_url=None if fast else "https://example.test/story#external",
        kind="news",
        title=title,
        publisher=issuer,
        tickers=["005930"],
        published_at=origin,
        detail_status="ok",
        body="합성 본문",
    )
    grouped = item.model_copy(
        update={
            "id": "other",
            "source_url": None,
            "title": "두 번째 개별 기사",
            "group_headline": "첫 묶음",
        }
    )

    def save(at, story):
        store.save(
            Capture(
                stream="news_all",
                page_url="https://www.saveticker.com/news",
                observed_at=at,
                items=[story, grouped],
            )
        )

    def listed():
        return client.get("/api/news?market=KR&ticker=005930").json()["items"]

    save(origin, item)
    conn = db.connect(project / "state" / "trader.db")
    try:
        kwargs = {"completed_since": origin} if fast else {}
        assert (
            saveticker_ingest.ingest_news(conn, root, now=lambda: origin, **kwargs)["inserted"] == 2
        )
        originals = news_store.recent(conn, "KR", asof=origin)
        original = next(r for r in originals if r["title"] == title)
        other = next(r for r in originals if r["title"] != title)
        assert listed() == [{**r, "title": "첫 묶음"} if r == other else r for r in originals]
        before = FEATURE.sse_keys[0].probe(client.app.state.deps)
        for minute, headline in (
            (1, "묶음 첫 제목"),
            (2, "동일 시각 첫 관측"),
            (2, "바뀐 묶음 제목"),
            (3, None),
        ):
            at = origin + timedelta(minutes=minute)
            save(at, item.model_copy(update={"group_headline": headline}))
        assert saveticker_ingest.ingest_news(conn, root, now=lambda: NOW, **kwargs)["inserted"] == 0
        for minute, expected in (
            (0, title),
            (1, "묶음 첫 제목"),
            (2, "바뀐 묶음 제목"),
            (3, title),
        ):
            clock[0] = origin + timedelta(minutes=minute)
            result = {r["id"]: r for r in listed()}
            assert result[original["id"]] == {**original, "title": expected}
        assert FEATURE.sse_keys[0].probe(client.app.state.deps) != before
        assert news_store.recent(conn, "KR", asof=NOW) == originals
        assert news_store.count(conn, "KR") == 2
    finally:
        conn.close()


def test_news_events_lists_disclosures_with_severity(project):
    c = _client(project)
    _seed(project)
    r = c.get("/api/news/events?market=KR").json()
    assert [(i["source"], i["severity"], i["tickers"]) for i in r["disclosures"]] == [
        ("dart", "critical", ["005930"])
    ]
    assert r["market"] == "KR" and r["asof"] == NOW.isoformat()
    assert c.get("/api/news/events?market=JP").status_code == 400


def test_news_list_keeps_ledger_title_if_capture_is_unavailable_or_different(project):
    client = _client(project)
    root = project / "state" / "external" / "saveticker" / "data"
    individual = "원장의 개별 속보"
    conn = db.connect(project / "state" / "trader.db")
    try:
        news_store.insert_items(
            conn,
            [
                {
                    "url": "https://www.saveticker.com/news#same",
                    "title": individual,
                    "published_at": NOW.isoformat(),
                    "received_at": NOW.isoformat(),
                }
            ],
            market="KR",
            source="saveticker:원장 출처",
            query="news_all",
            lang="ko",
            names={},
        )
    finally:
        conn.close()

    def title():
        return client.get("/api/news?market=KR").json()["items"][0]["title"]

    assert title() == individual
    store = Store(root)
    story = Item(
        id="same",
        kind="news",
        title=individual,
        group_headline="다른 묶음",
        publisher="다른 출처",
        published_at=NOW,
    )
    store.save(
        Capture(
            stream="news_all",
            page_url="https://www.saveticker.com/news",
            observed_at=NOW,
            items=[
                story.model_copy(update={"publisher": "원장 출처", "group_headline": "이전 묶음"})
            ],
        )
    )
    assert title() == "이전 묶음"
    for item in (
        story,
        story.model_copy(
            update={"publisher": "원장 출처", "published_at": NOW - timedelta(minutes=1)}
        ),
    ):
        store.save(
            Capture(
                stream="news_all",
                page_url="https://www.saveticker.com/news",
                observed_at=NOW,
                items=[item],
            )
        )
        assert title() == individual
    # 같은 발행처·개별 제목·시각의 HTML 기사도 실제 원문 주소가 다르면 다른 기사다.
    external_url = "https://example.test/article/one"
    conn = db.connect(project / "state" / "trader.db")
    try:
        conn.execute(
            "UPDATE news_items SET url=?, url_hash=? WHERE query='news_all'",
            (external_url, news_store.url_hash(external_url, "KR")),
        )
        conn.commit()
    finally:
        conn.close()
    foreign = story.model_copy(
        update={
            "id": identity("news", "원장 출처", individual),
            "publisher": "원장 출처",
            "source_url": "https://example.test/article/two",
        }
    )
    store.save(
        Capture(
            stream="news_all",
            page_url="https://www.saveticker.com/news",
            observed_at=NOW,
            items=[foreign],
        )
    )
    assert title() == individual
    (root / "feed.sqlite3").write_bytes(b"invalid fixture")
    assert title() == individual


def test_manual_inputs_requires_token_and_validates(project):
    c = _client(project)
    body = {"market": "KR", "text": "합성 메모 제목"}
    assert c.post("/api/news/manual", json=body).status_code == 401
    assert c.post("/api/news/manual", json={**body, "text": "  \n "}, headers=AUTH
                  ).status_code == 400  # fmt: skip
    r = c.post("/api/news/manual", json={**body, "url": "ftp://x"}, headers=AUTH)
    assert r.status_code == 400
    assert c.post("/api/news/manual", json={**body, "market": "JP"}, headers=AUTH
                  ).status_code == 422  # fmt: skip
    assert c.post("/api/news/manual", json={**body, "x": 1}, headers=AUTH).status_code == 422
    assert c.get("/api/news/manual").status_code == 422  # market 필수


def test_manual_inputs_roundtrip(project):
    c = _client(project)
    body = {
        "market": "KR",
        "text": "합성 메모 제목\n읽은 곳 메모 한 줄",
        "url": "https://example.test/manual/1",
    }
    r = c.post("/api/news/manual", json=body, headers=AUTH)
    assert r.status_code == 200 and r.json()["received_at"] == NOW.isoformat()
    g = c.get("/api/news/manual?market=KR").json()
    assert g["items"][0]["title"] == "합성 메모 제목"
    assert g["items"][0]["note"] == "읽은 곳 메모 한 줄"
    assert c.get("/api/news/manual?market=US").json()["items"] == []
    assert (project / "state" / "manual_inputs" / "_market_KR.md").is_file()
    assert manual_inputs.read_market("KR")[0]["url"] == body["url"]


def test_news_sse_key_tracks_store(project):
    _client(project)
    from tests.kernel.web.conftest import make_deps

    deps = make_deps(project, conn_factory=lambda: db.connect(project / "state" / "trader.db"))
    probe = {k.key: k for k in FEATURE.sse_keys}["news"]
    assert probe.noisy is False
    s0 = probe.probe(deps)
    _seed(project)
    assert probe.probe(deps) != s0


def test_nav_item():
    assert [(n.screen, n.id, n.label, n.order) for n in FEATURE.nav] == [
        ("data", "news", "뉴스·공시", 10)
    ]


def test_manual_text_length_capped(project):
    body = {"market": "KR", "text": "가" * 4001}
    assert _client(project).post("/api/news/manual", json=body, headers=AUTH).status_code == 422


def test_api_news_filters_by_source_group(project):
    """출처 필터: 많은 네이버·구글 기사에 세이브티커가 밀려도 따로 최신순으로 볼 수 있다."""
    c = _client(project)
    _seed(project)
    conn = db.connect(project / "state" / "trader.db")
    try:
        news_store.insert_items(
            conn,
            [{"title": "합성 세이브티커 기사", "url": "https://example.test/s1",
              "published_at": (NOW - timedelta(hours=3)).isoformat(),
              "received_at": (NOW - timedelta(hours=3)).isoformat()}],
            market="KR", source="saveticker:로이터", query="news_all", lang="ko", names={},
        )  # fmt: skip
    finally:
        conn.close()
    titles = lambda q: [i["title"] for i in c.get(f"/api/news?market=KR{q}").json()["items"]]  # noqa: E731
    assert titles("&source=saveticker") == ["합성 세이브티커 기사"]
    assert titles("&source=google_rss") == ["가나전자 합성 기사 1"]
    assert titles("&source=disclosure") == ["합성 공시 제목"]
    assert titles("&source=naver") == []
    assert titles("&source=saveticker&limit=1&ticker=005930") == []
    assert len(titles("")) == 3
    assert c.get("/api/news?market=KR&source=evil").status_code == 422
