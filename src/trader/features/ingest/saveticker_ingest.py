"""세이브티커 jsonl → 시장 전체 뉴스 저장소(`news_items`) 멱등 적재.

읽기(리더)는 `trader.features.saveticker.api` 가 맡고, 여기서는 적재용 dict 변환과 적재만 한다."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from trader.features.ingest import news_store
from trader.features.saveticker.api import (
    JSONL_DAYS,
    SUMMARY_CHARS,
    available,
    markets_of,
    published_at_of,
    read_news_items,
    report_excerpt,
)
from trader.kernel.config import KST


def to_store_item(root: Path, item: dict) -> dict:
    """세이브티커 항목 → news_items 적재용 dict (`news_store.insert_items` 입력)."""
    is_report = item.get("kind") == "report" or item.get("_stream") == "news_reports"
    title = str(item.get("title") or "").strip()
    publisher = str(item.get("publisher") or item.get("source_group") or "saveticker").strip()
    if is_report:
        title = f"SAVE 데일리 리포트 {item.get('report_date') or title}".strip()
        summary = report_excerpt(root, item, SUMMARY_CHARS)
        published = (
            datetime.fromisoformat(f"{item['report_date']}T08:00:00+09:00")
            .astimezone(UTC)
            .isoformat()
            if item.get("report_date")
            else None
        )
    else:
        summary = re.sub(r"\s+", " ", str(item.get("body") or "")).strip()[:SUMMARY_CHARS]
        published = published_at_of(item)
    # 리포트의 source_url 은 목록 페이지(전부 같다) → 날짜별 합성 URL 로 멱등 키를 만든다
    if is_report:
        url = f"https://www.saveticker.com/news#report-{item.get('report_date') or item.get('id')}"
    else:
        url = (item.get("source_url") or "").strip() or (
            f"https://www.saveticker.com/news#{item.get('id')}"
        )
    return {
        "title": title,
        "summary": summary,
        "url": url,
        "published_at": published,
        "received_at": item.get("_received_at"),
        "tickers": [str(t) for t in item.get("tickers") or []],
        "publisher": publisher,
        "categories": list(item.get("categories") or []),
        "stream": item.get("_stream"),
    }


def ingest_news(
    conn: sqlite3.Connection,
    root: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    names_by_market: dict[str, dict[str, str]] | None = None,
    days: int = JSONL_DAYS,
) -> dict:
    """뉴스·리포트를 시장 전체 뉴스 저장소에 멱등 적재한다. 폴더가 없으면 skipped."""
    if not available(root):
        return {"skipped": "saveticker 폴더 없음"}
    today = now().astimezone(KST).date()
    items = read_news_items(root, today=today, days=days)
    inserted = 0
    by_market: dict[str, int] = {}
    for raw in items:
        st = to_store_item(root, raw)
        if not st["title"]:
            continue
        for market in markets_of(raw):
            n = news_store.insert_items(
                conn,
                [st],
                market=market,
                source=f"saveticker:{st['publisher']}",
                query=str(st.get("stream") or "news"),
                lang="ko",
                names=(names_by_market or {}).get(market, {}),
                extra_tickers=True,
            )
            inserted += n
            by_market[market] = by_market.get(market, 0) + n
    return {"items": len(items), "inserted": inserted, "by_market": by_market}
