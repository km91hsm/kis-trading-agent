"""런타임 관심종목(`/watch`, 웹): control 플래그 `watchlist`.
universe.watch 와 합쳐 scan 후보·분석 대상에 들어간다."""

from __future__ import annotations

import re
import sqlite3

from trader.kernel.ledger.control import get_flag, log_change, set_flag
from trader.kernel.ledger.db import now_iso

_TICKER_RE = re.compile(r"^(?:(?:KR|US):)?[A-Z0-9.\-]{1,12}$")  # 선택적 시장 접두사
_KR_CODE_RE = re.compile(r"^\d{6}$")


def split_market(item: str) -> tuple[str, str]:
    """`KR:005930` → ("KR", "005930"); 접두사가 없으면 6자리 숫자는 KR, 그 외 US."""
    if ":" in item:
        m, t = item.split(":", 1)
        return m, t
    return ("KR" if _KR_CODE_RE.match(item) else "US"), item


def get_watchlist(conn: sqlite3.Connection, market: str | None = None) -> list[str]:
    """런타임 관심종목(`/watch`, 웹). universe.watch 와 합쳐 scan 후보·분석 대상에 들어간다.

    market 을 주면 그 시장 종목만 접두사 없이 돌려준다(접두사 없는 항목은 코드 형식으로 추정).
    """
    v = get_flag(conn, "watchlist", [])
    items = [str(t) for t in v] if isinstance(v, list) else []
    if market is None:
        return items
    return [t for m, t in (split_market(i) for i in items) if m == market]


def set_watchlist(
    conn: sqlite3.Connection,
    tickers: list[str],
    *,
    channel: str = "cli",
    actor: str | None = None,
) -> list[str]:
    """형식 검증(영숫자·.·- 12자 이내, 선택적 `KR:`/`US:` 접두사, 대문자화) 후 저장.

    같은 (시장, 종목) 은 처음 쓴 표기 하나만 남긴다(`005930` 과 `KR:005930` 은 같은 항목).
    """
    out: list[str] = []
    keys: set[tuple[str, str]] = set()
    for t in tickers:
        u = str(t).strip().upper()
        if not _TICKER_RE.match(u):
            raise ValueError(f"잘못된 종목코드: {t}")
        key = split_market(u)
        if key not in keys:
            keys.add(key)
            out.append(u)
    old = get_watchlist(conn)
    set_flag(conn, "watchlist", out)
    if old != out:
        log_change(
            conn, channel=channel, actor=actor, key="watchlist", old=old, new=out, at=now_iso()
        )
    return out


def remove_watch(conn: sqlite3.Connection, ticker: str) -> list[str]:
    """접두사 유무와 무관하게 같은 종목을 제거한다 (`000660` 으로 `KR:000660` 도 지움)."""
    u = str(ticker).strip().upper()
    _, want = split_market(u)
    market = u.split(":", 1)[0] if ":" in u else None
    keep = []
    for item in get_watchlist(conn):
        m, t = split_market(item)
        if t == want and (market is None or m == market):
            continue
        keep.append(item)
    return set_watchlist(conn, keep)
