"""세이브티커 jsonl → 시장 전체 뉴스 저장소(`news_items`) 멱등 적재.

읽기(리더)는 `trader.features.saveticker.api` 가 맡고, 여기서는 적재용 dict 변환과 적재만 한다."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from trader.features.ingest import news_store
from trader.features.saveticker.api import (
    JSONL_DAYS,
    NEWS_STREAMS,
    SUMMARY_CHARS,
    available,
    kr_ticker_code,
    published_at_of,
    read_completed_news,
    read_news_items,
    report_excerpt,
    tickers_by_market,
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
        "tickers": [str(t).strip() for t in item.get("tickers") or [] if t and str(t).strip()],
        "publisher": publisher,
        "categories": list(item.get("categories") or []),
        "stream": item.get("_stream"),
    }


def _stored_tags(encoded: str) -> set[str] | None:
    tags = json.loads(encoded)
    if isinstance(tags, list) and all(isinstance(t, str) for t in tags):
        return set(tags)
    return None


def _replace_tags(conn: sqlite3.Connection, row_id: int, old_json: str, tags: set[str]) -> bool:
    """다른 수집 작업이 먼저 보정한 행은 다음 주기에 다시 확인한다."""
    return bool(
        conn.execute(
            "UPDATE news_items SET tickers_json=? WHERE id=? AND tickers_json=?",
            (json.dumps(sorted(tags), ensure_ascii=False), row_id, old_json),
        ).rowcount
    )


def _correct_existing_tickers(
    conn: sqlite3.Connection, url: str, market: str, source: str, query: str, raw_tickers: set[str]
) -> None:
    """동일 원문과 대조할 수 있는 옛 태그만 보정하고, 본문에서 따로 추론한 태그는 보존한다."""
    row = conn.execute(
        "SELECT id, tickers_json FROM news_items "
        "WHERE url_hash=? AND source=? AND query=? AND json_valid(tickers_json)",
        (news_store.url_hash(url, market), source, query),
    ).fetchone()
    if row is None:
        return
    saved = _stored_tags(row[1]) or set()
    matched = saved & raw_tickers
    if not matched:
        return
    normalized = tickers_by_market({"tickers": matched}).get(market, [])
    corrected = (saved - matched) | set(normalized)
    if corrected != saved:
        _replace_tags(conn, row[0], row[1], corrected)
        conn.commit()


def _restore_kr_article(conn: sqlite3.Connection, row: sqlite3.Row | tuple) -> int:
    row_id, url, source, query, known_at, encoded = row
    tags = _stored_tags(encoded) or set()
    codes = {t: code for t in tags if (code := kr_ticker_code(t))}
    if not any(t.strip().upper().endswith(".KR") for t in codes):
        return 0
    kr_hash = news_store.url_hash(url, "KR")
    peer = conn.execute(
        "SELECT id, source, query, MAX(received_at, COALESCE(published_at, received_at)), "
        "tickers_json FROM news_items WHERE url_hash=? AND json_valid(tickers_json)",
        (kr_hash,),
    ).fetchone()
    inserted = 0
    if peer is None:
        confirmed = set(codes.values())
        inserted = conn.execute(
            "INSERT INTO news_items(market, source, query, title, url, url_hash, published_at, "
            "received_at, summary, tickers_json, lang, severity) "
            "SELECT 'KR', source, query, title, url, ?, published_at, received_at, summary, "
            "?, lang, severity FROM news_items WHERE id=? AND tickers_json=? "
            "ON CONFLICT(url_hash) DO NOTHING",
            (kr_hash, json.dumps(sorted(confirmed)), row_id, encoded),
        ).rowcount
        if not inserted:
            return 0
    else:
        peer_id, peer_source, peer_query, peer_known, peer_json = peer
        if (source, query) != (peer_source, peer_query):
            return 0
        previous = _stored_tags(peer_json)
        if previous is None:
            return 0
        confirmed = {kr_ticker_code(t) or t for t in previous}
        if known_at <= peer_known:
            confirmed.update(codes.values())
        if confirmed != previous and not _replace_tags(conn, peer_id, peer_json, confirmed):
            return 0
        if peer_known > known_at:
            return 0  # 먼저 수신된 US행에서도 당시 국내 태그를 계속 찾을 수 있어야 한다.
    # 국내 전용 US행의 태그를 비우면 무태그 거시 기사로 오인된다. 혼합 기사만 정리한다.
    if tags - codes.keys():
        remaining = tags - {t for t, code in codes.items() if code in confirmed}
        if remaining != tags:
            _replace_tags(conn, row_id, encoded, remaining)
    return inserted


def restore_kr_news(conn: sqlite3.Connection, *, since: datetime, asof: datetime) -> int:
    """보관 중인 세이브티커 US 기사에서 누락된 KR 행과 기존 KR 태그를 당시 기록으로 보완한다."""
    cutoff = asof.astimezone(UTC).isoformat()
    candidates = conn.execute(
        "SELECT id, url, source, query, MAX(received_at, COALESCE(published_at, received_at)), "
        "tickers_json FROM news_items WHERE market='US' "
        f"AND source GLOB 'saveticker:*' AND query IN ({','.join('?' * len(NEWS_STREAMS))}) "
        "AND received_at <= ? AND COALESCE(published_at, received_at) BETWEEN ? AND ? "
        "AND tickers_json LIKE '%.KR%' AND json_valid(tickers_json)",
        (*NEWS_STREAMS, cutoff, since.astimezone(UTC).isoformat(), cutoff),
    ).fetchall()
    with conn:
        return sum(_restore_kr_article(conn, row) for row in candidates)


def ingest_news(
    conn: sqlite3.Connection,
    root: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    names_by_market: dict[str, dict[str, str]] | None = None,
    days: int = JSONL_DAYS,
    completed_since: datetime | None = None,
) -> dict:
    """뉴스·리포트를 시장 전체 뉴스 저장소에 멱등 적재한다. 폴더가 없으면 skipped."""
    if not available(root):
        return {"skipped": "saveticker 폴더 없음"}
    asof = now()
    if completed_since is None:
        items = read_news_items(root, today=asof.astimezone(KST).date(), days=days)
    else:
        items = read_completed_news(root, since=completed_since, asof=asof)
    inserted = 0
    by_market: dict[str, int] = {}
    for raw in items:
        st = to_store_item(root, raw)
        if not st["title"]:
            continue
        source = f"saveticker:{st['publisher']}"
        query = str(st.get("stream") or "news")
        raw_tickers = set(st["tickers"])
        for market, tickers in tickers_by_market(raw).items():
            n = news_store.insert_items(
                conn,
                [{**st, "tickers": tickers}],
                market=market,
                source=source,
                query=query,
                lang="ko",
                names=(names_by_market or {}).get(market, {}),
                extra_tickers=True,
            )
            if not n and raw_tickers != set(tickers):
                _correct_existing_tickers(conn, st["url"], market, source, query, raw_tickers)
            inserted += n
            by_market[market] = by_market.get(market, 0) + n
    return {"items": len(items), "inserted": inserted, "by_market": by_market}
