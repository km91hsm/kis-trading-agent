"""quotes 데몬 잡 `names_fill`(interval, schedule.yaml `names_fill.interval_s`): 종목명 채우기.

1. 설정 `quotes.instrument_names`(`"<시장>:<종목>": 이름` — 헤지·현금·지수 대용 ETF 처럼 DART·EDGAR
   캐시에 없는 상품)를 원장 `ticker_names` 에 `source='config'` 로 적재한다. 이미 이름이 있는
   종목(잔고 관측 등)은 덮지 않는다.
2. 원장 `positions_snapshot`·`orders`(현물)에 나왔지만 `ticker_names` 에 없는 종목을 시세
   클라이언트 `get_stock_name` 으로 조회해 `source='broker'` 로 남긴다. 못 찾은 종목은 control
   `names_miss` 에 그날(KST) 날짜를 남겨 다시 묻지 않고, 한 번에 `LIMIT` 건까지만 묻는다.

`ticker_names`·`ticker_exchanges` 는 spot 표다(데이터 접근 — quotes 는 레인을 import 하지 않는다).
옛 `names.fill_missing`·데몬 `_names_fill_body` 이식. 화면 표시 경로는 이 잡을 부르지 않는다."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from trader.kernel.config import KST
from trader.kernel.ledger import control
from trader.kernel.ledger.db import now_iso
from trader.kernel.registry.types import JobSpec

logger = logging.getLogger("trader.quotes")

MISS_FLAG = "names_miss"  # {"<시장>:<종목>": "YYYY-MM-DD"} — 그날 브로커가 이름을 못 준 종목
LIMIT = 20  # 한 번에 조회하는 최대 종목 수


def unnamed_tickers(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """원장 보유 스냅샷·현물 주문에 나왔지만 `ticker_names` 에 이름이 없는 (시장, 종목).
    현금 행(`_CASH*`)·파생(asset='fo')은 뺀다."""
    rows = conn.execute(
        "SELECT market, ticker FROM ("
        " SELECT market, ticker FROM positions_snapshot"
        " WHERE asset = 'stock' AND ticker NOT LIKE '\\_CASH%' ESCAPE '\\'"
        " UNION SELECT market, ticker FROM orders WHERE asset = 'stock'"
        ") AS t WHERE market IN ('KR', 'US') AND NOT EXISTS ("
        " SELECT 1 FROM ticker_names n WHERE n.market = t.market AND n.ticker = t.ticker"
        ") ORDER BY market, ticker"
    ).fetchall()
    return [(str(r["market"]), str(r["ticker"])) for r in rows]


def _insert_name(
    conn: sqlite3.Connection, market: str, ticker: str, name: str | None, source: str
) -> bool:
    """이름이 없는 종목에만 적는다(관측 이름 우선). 빈 이름·코드와 같은 이름은 무시. 썼으면 True."""
    clean = (name or "").strip()
    if not clean or clean == ticker:
        return False
    cur = conn.execute(
        "INSERT INTO ticker_names(market, ticker, name, source, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(market, ticker) DO NOTHING",
        (market, ticker, clean, source, now_iso()),
    )
    conn.commit()
    return cur.rowcount > 0


def _us_exchanges(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT ticker, exchange FROM ticker_exchanges WHERE market = 'US'")
    return {str(r["ticker"]): str(r["exchange"]) for r in rows}


def _fill_config(conn: sqlite3.Connection, names: dict[str, str]) -> int:
    n = 0
    for key, name in names.items():
        market, ticker = key.split(":", 1)
        n += _insert_name(conn, market, ticker, name, "config")
    return n


def names_fill_body(deps: Any, job: Any = None) -> dict:
    """설정 고정 이름 적재 → 이름 없는 보유·주문 종목을 시세 클라이언트로 채운다."""
    cfg = deps.settings.quotes
    port = (deps.ports or {}).get("accounts")
    conn = deps.conn_factory()
    try:
        configured = _fill_config(conn, cfg.instrument_names)
        client = port.market_client() if port is not None else None
        if client is None:
            return {"skipped": "시세 클라이언트 없음", "config": configured}
        today = deps.now().astimezone(KST).date().isoformat()
        keys = unnamed_tickers(conn)
        misses = {
            k: v for k, v in (control.get_flag(conn, MISS_FLAG) or {}).items() if v == today
        }  # 지난 날 기록은 버린다
        exchanges = _us_exchanges(conn)
        asked = filled = 0
        for market, ticker in keys:
            tag = f"{market}:{ticker}"
            if tag in misses:
                continue
            if asked >= LIMIT:
                break
            asked += 1
            exchange = exchanges.get(ticker) if market == "US" else None
            try:
                name = client.get_stock_name(market, ticker, exchange=exchange)
            except Exception as e:  # noqa: BLE001 - 한 종목 실패가 나머지를 막지 않는다
                logger.warning("종목명 조회 실패 %s: %s", tag, type(e).__name__)
                name = None
            if _insert_name(conn, market, ticker, name, "broker"):
                filled += 1
            else:
                misses[tag] = today
        control.set_flag(conn, MISS_FLAG, misses)
        return {"config": configured, "candidates": len(keys), "asked": asked, "filled": filled}
    finally:
        conn.close()


NAMES_FILL = JobSpec(
    id="names_fill", kind="names_fill", trigger="interval", per_market=False,
    schedule_key="names_fill.interval_s", interval_s=3600, enabled=None, gate="none",
    heavy=False, misfire="default", catch_up="none", start_soon_s=120, body=names_fill_body,
)  # fmt: skip
