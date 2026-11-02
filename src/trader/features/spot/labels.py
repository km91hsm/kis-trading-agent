"""종목 표기: 이름이 있으면 `이름(코드)`, 없으면 코드."""

from __future__ import annotations

import sqlite3


def label(ticker: str, name: str | None) -> str:
    """사용자 대면 종목 표기."""
    return f"{name}({ticker})" if name else ticker


def resolve(conn: sqlite3.Connection, market: str, ticker: str) -> str | None:
    """원장 `ticker_names` 의 종목명(없으면 None). 네트워크를 타지 않는다."""
    row = conn.execute(
        "SELECT name FROM ticker_names WHERE market=? AND ticker=?", (market, ticker)
    ).fetchone()
    return str(row["name"]) if row else None
