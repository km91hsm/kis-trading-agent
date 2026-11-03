"""종목 표기: 이름이 있으면 `이름(코드)`, 없으면 코드. 이름은 원장 `ticker_names` → 캐시 JSON
(DART 기업코드 맵·EDGAR 티커 맵) 순으로 찾고, 어떤 경로도 네트워크를 타지 않는다."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("trader.names")

_CACHE_FILES = (("KR", "dart_corpcode.json", "corp_name"), ("US", "edgar_tickers.json", "name"))


def label(ticker: str, name: str | None) -> str:
    """사용자 대면 종목 표기."""
    return f"{name}({ticker})" if name else ticker


def resolve(conn: sqlite3.Connection, market: str, ticker: str) -> str | None:
    """원장 `ticker_names` 의 종목명(없으면 None). 네트워크를 타지 않는다."""
    row = conn.execute(
        "SELECT name FROM ticker_names WHERE market=? AND ticker=?", (market, ticker)
    ).fetchone()
    return str(row["name"]) if row else None


def load_cache_names(cache_dir: Path) -> dict[tuple[str, str], str]:
    """캐시 JSON 두 개를 직접 읽는다. 만료된 캐시도 이름은 유효하다. 없음·깨짐은 빈 dict."""
    out: dict[tuple[str, str], str] = {}
    for market, fname, key in _CACHE_FILES:
        p = cache_dir / fname
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            table = data.get("map") or {}
        except (OSError, ValueError, AttributeError) as e:
            logger.warning("종목명 캐시를 읽지 못함 %s: %s", p.name, e)
            continue
        for ticker, row in table.items():
            name = str((row or {}).get(key) or "").strip() if isinstance(row, dict) else ""
            if name and name != ticker:
                out[(market, str(ticker))] = name
    return out


def display(conn: sqlite3.Connection | None, cache_dir: Path, market: str, ticker: str) -> str:
    """표시용 라벨(원장 → 캐시 → 코드). 이름은 보조 정보라 원장 잠금·캐시 오류가 호출자를 막지
    않는다 — 어떤 예외도 코드만 돌려준다."""
    try:
        name = resolve(conn, market, ticker) if conn is not None else None
        if not name:
            name = load_cache_names(cache_dir).get((market, ticker))
        return label(ticker, name)
    except Exception as e:  # noqa: BLE001
        logger.debug("종목명 조회 실패 %s %s: %s", market, ticker, e)
        return label(ticker, None)
