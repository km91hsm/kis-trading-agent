"""ingest 웹 자원: `/news`(저장소 행 그대로, 토큰 없음)·`/news/events`(공시)·`/news/manual`(토큰).

옛 `tests/web/test_news_api.py` 1건·`test_manual_inputs_api.py` 2건 이식 + tickers 배열 유지.
fixture 는 구조만 본뜬 합성 행이다(실제 기사 제목 아님)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.features.conftest import AUTH, web_client
from trader.features.ingest import manual_inputs, news_store
from trader.features.ingest.feature import FEATURE
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


def test_news_events_lists_disclosures_with_severity(project):
    c = _client(project)
    _seed(project)
    r = c.get("/api/news/events?market=KR").json()
    assert [(i["source"], i["severity"], i["tickers"]) for i in r["disclosures"]] == [
        ("dart", "critical", ["005930"])
    ]
    assert r["market"] == "KR" and r["asof"] == NOW.isoformat()
    assert c.get("/api/news/events?market=JP").status_code == 400


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
        ("data", "news", "뉴스·이벤트", 10)
    ]
