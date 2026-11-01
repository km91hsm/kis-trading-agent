"""세이브티커 jsonl → news_items 적재(features/ingest/saveticker_ingest)."""

import shutil
from datetime import UTC, datetime
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
