"""`/names*` 웹 자원(조회 전용, 토큰 없음, 네트워크 없음): 종목명 조회·부분 일치 검색.

이름은 원장 `ticker_names`(spot 표, 데이터 접근) → 캐시 JSON(`state/cache` 의 DART·EDGAR 맵,
`kernel.ledger.names.load_cache_names`) 순. 웹은 읽기 전용이라 캐시 적중을 원장에 쓰지 않는다."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from trader.features.ingest import api as ingest
from trader.features.quotes.routes import _TICKER_RE
from trader.features.scan import api as scan
from trader.kernel.ledger.names import load_cache_names

router = APIRouter(tags=["names"])

_SOURCE_RANK = {"held": 0, "watch": 1, "universe": 2, "ledger": 3, "listed": 4}


def _cache_dir(deps: Any) -> Path:
    return Path(deps.state) / "cache"


def _ledger_names(
    conn: sqlite3.Connection, market: str | None = None
) -> dict[tuple[str, str], str]:
    sql, args = "SELECT market, ticker, name FROM ticker_names", ()
    if market is not None:
        sql, args = sql + " WHERE market=?", (market,)
    return {(r["market"], r["ticker"]): r["name"] for r in conn.execute(sql, args)}


@router.get("/names")
def ticker_names(
    request: Request,
    market: str | None = Query(None),
    tickers: str | None = Query(None, description="쉼표 구분. 없으면 원장 전체"),
) -> dict[str, str]:
    """`{"<시장>:<종목>": 이름}`. 모르는 종목은 키가 없다. tickers 를 주면 market 필수."""
    deps = request.app.state.deps
    if tickers and market not in ("KR", "US"):
        raise HTTPException(status_code=400, detail="tickers 를 주면 market 은 KR|US 필수")
    with closing(deps.conn_factory()) as conn:
        if not tickers:
            found = _ledger_names(conn, market)
        else:
            keys = []
            for t in (x.strip() for x in tickers.split(",")):
                if not t:
                    continue
                if not _TICKER_RE.fullmatch(t):
                    raise HTTPException(status_code=400, detail=f"ticker 형식 오류: {t!r}")
                keys.append((market, t))
            ledger = _ledger_names(conn, market)
            cache: dict[tuple[str, str], str] | None = None
            found = {}
            for key in keys:
                name = ledger.get(key)
                if name is None:
                    if cache is None:
                        cache = load_cache_names(_cache_dir(deps))
                    name = cache.get(key)
                if name:
                    found[key] = name
    return {f"{m}:{t}": n for (m, t), n in found.items()}


def _norm(s: str) -> str:
    return "".join(s.split()).casefold()


def _universe() -> dict[tuple[str, str], str | None]:
    try:
        u = ingest.load_universe()
    except Exception:  # noqa: BLE001 - 유니버스 파일이 없거나 깨져도 검색은 된다
        return {}
    return {**{("KR", t): None for t in u.kr}, **{("US", s.symbol): s.exchange for s in u.us}}


@router.get("/names/search")
def search_names(
    request: Request,
    q: str = Query("", max_length=40),
    market: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
) -> list[dict]:
    """종목 코드·이름 부분 일치. 대상은 보유·관심·유니버스·원장 이름·캐시(상장 전체).
    순서: 코드 접두 > 이름 접두 > 이름 부분 > 코드 부분, 같으면 보유 > 관심 > 유니버스 > 원장 >
    상장. 빈 질의는 빈 목록."""
    if market not in (None, "KR", "US"):
        raise HTTPException(status_code=400, detail="market 은 KR|US")
    nq = _norm(q)
    if not nq:
        return []
    deps = request.app.state.deps
    universe = _universe()
    with closing(deps.conn_factory()) as conn:
        held = {
            (r["market"], r["ticker"])
            for r in conn.execute(
                "SELECT DISTINCT market, ticker FROM positions_snapshot WHERE qty > 0 "
                "AND ticker NOT LIKE '\\_CASH%' ESCAPE '\\'"
            )
        }
        watch = {(m, t) for m in ("KR", "US") for t in scan.get_watchlist(conn, market=m)}
        ledger = _ledger_names(conn)
    names_map = {**load_cache_names(_cache_dir(deps)), **ledger}
    scored = []
    for m, t in set(names_map) | set(universe) | held | watch:
        if market and m != market:
            continue
        name = names_map.get((m, t)) or ""
        nt, nn = t.casefold(), _norm(name)
        if nt.startswith(nq):
            rank = 0
        elif nn.startswith(nq):
            rank = 1
        elif nq in nn:
            rank = 2
        elif nq in nt:
            rank = 3
        else:
            continue
        if (m, t) in held:
            source = "held"
        elif (m, t) in watch:
            source = "watch"
        elif (m, t) in universe:
            source = "universe"
        elif (m, t) in ledger:
            source = "ledger"
        else:
            source = "listed"
        scored.append((rank, _SOURCE_RANK[source], len(name), t, m, name, source))
    scored.sort()
    return [
        {"market": m, "ticker": t, "name": name or None, "exchange": universe.get((m, t)),
         "source": src}
        for _, _, _, t, m, name, src in scored[:limit]
    ]  # fmt: skip
