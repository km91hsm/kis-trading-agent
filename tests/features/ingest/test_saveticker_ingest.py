"""세이브티커 jsonl → news_items 적재(features/ingest/saveticker_ingest)."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trader.features.ingest import news_store, saveticker_ingest
from trader.features.saveticker.api import read_news_items

SAMPLE = Path(__file__).parent.parent / "saveticker" / "fixtures" / "saveticker"
T_AFTER = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)  # 샘플 관측 뒤


def _root(tmp_path: Path) -> Path:
    dst = tmp_path / "saveticker"
    shutil.copytree(SAMPLE, dst)
    return dst


def test_to_store_item_fields(tmp_path):
    root = _root(tmp_path)
    items = read_news_items(root, today=T_AFTER.date())
    report = next(i for i in items if i["_stream"] == "news_reports")
    st = saveticker_ingest.to_store_item(root, report)
    assert st["title"].startswith("SAVE 데일리 리포트 2026-10-23") and "S&P500" in st["summary"]
    assert st["published_at"] == "2026-10-22T23:00:00+00:00" and st["url"].startswith(
        "https://www.saveticker.com/news#"
    )
    news = next(i for i in items if i["_stream"] == "news_all" and i.get("tickers"))
    st2 = saveticker_ingest.to_store_item(root, news)
    assert st2["tickers"] and st2["publisher"] and st2["received_at"]


def test_ingest_news_is_idempotent_and_splits_markets(conn, tmp_path):
    root = _root(tmp_path)
    r = saveticker_ingest.ingest_news(conn, root, now=lambda: T_AFTER)
    assert r["items"] > 0 and r["inserted"] > r["items"]  # 티커 없는 거시 뉴스는 KR·US 두 행
    assert set(r["by_market"]) == {"KR", "US"}
    assert saveticker_ingest.ingest_news(conn, root, now=lambda: T_AFTER)["inserted"] == 0
    us = news_store.recent(conn, "US", asof=T_AFTER, hours=24 * 30, limit=200)
    assert any(i["source"].startswith("saveticker:") for i in us)
    gsk = news_store.recent(conn, "US", asof=T_AFTER, hours=24 * 30, ticker="GSK")
    assert gsk and all("GSK" in i["tickers"] for i in gsk)
    # 세이브티커 티커가 붙은 US 뉴스는 KR 행이 없다
    assert not any(
        "GSK" in i["tickers"]
        for i in news_store.recent(conn, "KR", asof=T_AFTER, hours=24 * 30, limit=200)
    )


def test_ingest_news_skips_when_root_missing(conn, tmp_path):
    assert saveticker_ingest.ingest_news(conn, tmp_path / "none", now=lambda: T_AFTER) == {
        "skipped": "saveticker 폴더 없음"
    }


def _tagged_article(root, tickers):
    day = root / "data" / T_AFTER.date().isoformat()
    day.mkdir(parents=True)
    row = {
        "stream": "news_all",
        "received_at": T_AFTER.isoformat(),
        "revision": 1,
        "item": {"id": "mixed-1", "kind": "news", "title": "합성 제목", "tickers": tickers},
    }
    (day / "news.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_ingest_queries_only_each_markets_normalized_tickers(conn, tmp_path):
    tickers = ["005930", "005930.KR", "0256C0.KR", "brk.b"]
    _tagged_article(tmp_path, tickers)
    saveticker_ingest.ingest_news(conn, tmp_path, now=lambda: T_AFTER)

    kr = news_store.recent(conn, "KR", asof=T_AFTER, ticker="0256C0")
    us = news_store.recent(conn, "US", asof=T_AFTER, ticker="BRK.B")
    assert [r["tickers"] for r in kr] == [["005930", "0256C0"]]
    assert [r["tickers"] for r in us] == [["BRK.B"]]
    assert read_news_items(tmp_path, today=T_AFTER.date())[0]["tickers"] == tickers


def test_reingest_legacy_us_article_restores_korean_lookup_and_keeps_extra_tags(conn, tmp_path):
    _tagged_article(tmp_path, ["0256C0.KR", "BRK.B"])
    news_store.insert_items(
        conn,
        [
            {
                "url": "https://www.saveticker.com/news#mixed-1",
                "title": "합성 제목",
                "received_at": T_AFTER.isoformat(),
                "tickers": ["0256C0.KR", "BRK.B", "HIST"],
            }
        ],
        market="US",
        source="saveticker:saveticker",
        query="news_all",
        lang="ko",
        names={},
        extra_tickers=True,
    )
    existing_id = news_store.recent(conn, "US", asof=T_AFTER)[0]["id"]
    first = saveticker_ingest.ingest_news(conn, tmp_path, now=lambda: T_AFTER)
    assert first["inserted"] == 1
    us = news_store.recent(conn, "US", asof=T_AFTER, ticker="BRK.B")
    assert [(r["id"], r["tickers"]) for r in us] == [(existing_id, ["BRK.B", "HIST"])]
    assert news_store.recent(conn, "KR", asof=T_AFTER, ticker="0256C0")
    assert saveticker_ingest.ingest_news(conn, tmp_path, now=lambda: T_AFTER)["inserted"] == 0


def _put_stored(conn, market, source, url, tickers, *, received=T_AFTER):
    item = {
        "url": url,
        "title": "합성 제목",
        "summary": f"{market} 원문 요약",
        "received_at": received.isoformat(),
        "tickers": tickers,
    }
    news_store.insert_items(
        conn,
        [item],
        market=market,
        source=source,
        query="news_all",
        lang="ko",
        names={},
        extra_tickers=True,
    )


def test_restore_preserves_existing_kr_urls_and_skips_unproven_tags(conn):

    _put_stored(conn, "US", "saveticker:test", "https://example.org/domestic", ["0256C0.KR"])
    _put_stored(
        conn, "US", "saveticker:test", "https://example.org/collision", ["0256C0.KR", "TEST"]
    )
    _put_stored(conn, "KR", "other", "https://example.org/collision", ["004321"])
    _put_stored(
        conn, "US", "saveticker:test", "https://example.org/ambiguous", ["BRK.KR", "005930"]
    )
    for expected in (1, 0):
        assert (
            saveticker_ingest.restore_kr_news(
                conn, since=T_AFTER - timedelta(days=14), asof=T_AFTER
            )
            == expected
        )
        rows = news_store.recent(conn, "KR", asof=T_AFTER)
        assert {r["url"]: r["tickers"] for r in rows} == {
            "https://example.org/domestic": ["0256C0"],
            "https://example.org/collision": ["004321"],
        }
        us = news_store.recent(conn, "US", asof=T_AFTER)
        assert all(r["tickers"] for r in us)
        assert [r["tickers"] for r in us if r["url"].endswith("/collision")] == [
            ["0256C0.KR", "TEST"]
        ]


def test_restore_normalizes_existing_kr_article_with_same_source_and_query(conn):
    url = "https://example.org/existing"
    received = T_AFTER - timedelta(days=8)
    _put_stored(conn, "KR", "saveticker:test", url, ["005930", "0256C0.KR"], received=received)
    _put_stored(
        conn, "US", "saveticker:test", url, ["0256C0.KR", "004321.KR", "USX"], received=received
    )
    original = news_store.recent(conn, "KR", asof=received)[0]
    for _ in range(2):
        assert (
            saveticker_ingest.restore_kr_news(
                conn, since=T_AFTER - timedelta(days=14), asof=T_AFTER
            )
            == 0
        )
        assert news_store.recent(conn, "KR", asof=received, ticker="0256C0") == [
            {**original, "tickers": ["004321", "005930", "0256C0"]}
        ]
        assert news_store.recent(conn, "US", asof=received)[0]["tickers"] == ["USX"]


def test_restore_keeps_later_us_tags_out_of_earlier_kr_article(conn):
    url = "https://example.org/later"
    early = T_AFTER - timedelta(days=8)
    _put_stored(conn, "KR", "saveticker:test", url, ["0256C0.KR"], received=early)
    _put_stored(conn, "US", "saveticker:test", url, ["0256C0.KR", "004321.KR", "USX"])
    saveticker_ingest.restore_kr_news(conn, since=T_AFTER - timedelta(days=14), asof=T_AFTER)
    assert news_store.recent(conn, "KR", asof=early)[0]["tickers"] == ["0256C0"]
    assert news_store.recent(conn, "US", asof=T_AFTER)[0]["tickers"] == ["004321.KR", "USX"]
