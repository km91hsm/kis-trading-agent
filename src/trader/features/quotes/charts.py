"""`/charts*` 웹 자원(조회 전용, 토큰 없음): KIS 일봉·당일 분봉 + 체결 마커 + 손절·목표 선.

- 봉은 quotes 런타임의 시세 클라이언트(`routes.runtime_for`)로 받는다(두 번째 클라이언트를 만들지
  않는다). 클라이언트가 없으면 503, KIS 오류는 502.
- quotes 는 레인을 import 하지 않으므로 마커는 원장 `fills`(+ `orders` 의 종목·방향)에서, 선 값은
  그 종목 최신 결정 `decisions.data_json` 의 `stop_loss`/`take_profit` 에서 읽는다(원장 표 접근).
- 일봉은 (시장·종목·거래소·일수·KST 날짜) 키로 300초, 분봉은 (시장·종목·거래소·개수) 키로 60초
  캐시. 분봉 캐시 미스는 키별 락 안에서 한 번 더 확인해 동시 요청도 KIS 1회.
- 분봉 시간은 UTC epoch 초(lightweight-charts 형식). KR 은 서울, US 는 뉴욕 현지 시각을 변환한다.
"""

from __future__ import annotations

import threading
import time
from contextlib import closing
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request

from trader.features.quotes.routes import _TICKER_RE, US_EXCHANGES, _us_exchanges, runtime_for
from trader.kernel.broker.errors import KisApiError
from trader.kernel.config import KST
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.execution.ledger import day_bounds

router = APIRouter(prefix="/charts", tags=["charts"])

CACHE_TTL_S = 300.0
MINUTE_CACHE_TTL_S = 60.0
US_MINUTE_MAX = 120
_candle_cache: dict[tuple, tuple[float, list[dict]]] = {}
_minute_cache: dict[tuple, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()  # 요청 스레드가 여럿이라 dict 순회·삭제가 겹친다
_minute_locks: dict[tuple, threading.Lock] = {}  # 분봉 키별 조회 락(동시 미스를 KIS 1회로)


def reset() -> None:
    """차트 캐시·락을 비운다(테스트용)."""
    with _cache_lock:
        _candle_cache.clear()
        _minute_cache.clear()
        _minute_locks.clear()


def _remember(cache: dict, key: tuple, value: list[dict], *, ttl: float) -> None:
    """저장하면서 만료된 항목을 지운다(일봉 키에 KST 날짜가 들어가 날마다 늘어난다)."""
    t = time.monotonic()
    with _cache_lock:
        for k in [k for k, (saved, _) in cache.items() if t - saved >= ttl]:
            del cache[k]
        cache[key] = (t, value)


def _hit(cache: dict, key: tuple, ttl: float) -> list[dict] | None:
    with _cache_lock:
        hit = cache.get(key)
    return hit[1] if hit and time.monotonic() - hit[0] < ttl else None


def _validate(request: Request, ticker: str, market: str, exchange: str | None):
    """market → 티커 형식 → US 거래소 → 시세 클라이언트. US 거래소가 없으면 유니버스(없으면 NAS)."""
    if market not in ("KR", "US"):
        raise HTTPException(status_code=400, detail="market 은 KR|US")
    if not _TICKER_RE.fullmatch(ticker):
        raise HTTPException(status_code=400, detail=f"ticker 형식 오류: {ticker!r}")
    if market == "US":
        if exchange is None:
            exchange = _us_exchanges().get(ticker, "NAS")
        elif exchange not in US_EXCHANGES:
            raise HTTPException(status_code=400, detail="exchange 는 NAS|NYS|AMS")
    else:
        exchange = None
    deps = request.app.state.deps
    client = runtime_for(deps).market
    if client is None:
        raise HTTPException(status_code=503, detail="시세 클라이언트 없음 (공용 실전 키 필요)")
    return deps, client, exchange


def _fetch(call: Any) -> list:
    try:
        return call()
    except KisApiError as e:
        raise HTTPException(status_code=502, detail=f"시세 조회 실패: {e}") from None


def _bar(c: Any, t: Any) -> dict:
    return {
        "time": t,
        "open": str(c.open),
        "high": str(c.high),
        "low": str(c.low),
        "close": str(c.close),
        "volume": c.volume,
    }


def _fills(conn, market: str, ticker: str, lo: str | None = None, hi: str | None = None) -> list:
    """종목의 체결(+ 주문 계좌·방향). lo/hi 는 UTC ISO 경계 [lo, hi)."""
    sql = (
        "SELECT f.account_id, o.side, f.qty, f.price, f.filled_at FROM fills f "
        "JOIN orders o ON o.id = f.order_id WHERE o.market=? AND o.ticker=? AND o.asset='stock'"
    )
    args: list = [market, ticker]
    if lo is not None:
        sql += " AND f.filled_at >= ?"
        args.append(lo)
    if hi is not None:
        sql += " AND f.filled_at < ?"
        args.append(hi)
    return conn.execute(sql + " ORDER BY f.filled_at, f.id", args).fetchall()


def _lines(conn, market: str, ticker: str) -> dict:
    """그 종목 최신 결정의 손절·목표 값(같은 날 재평가는 data_json 만 바뀌므로 JSON 에서 읽는다)."""
    row = conn.execute(
        "SELECT date, json_extract(data_json, '$.stop_loss') AS sl, "
        "json_extract(data_json, '$.take_profit') AS tp FROM decisions "
        "WHERE market=? AND ticker=? ORDER BY date DESC, id DESC LIMIT 1",
        (market, ticker),
    ).fetchone()
    if row is None:
        return {"stop_loss": None, "take_profit": None, "date": None}
    return {
        "stop_loss": None if row["sl"] is None else str(row["sl"]),
        "take_profit": None if row["tp"] is None else str(row["tp"]),
        "date": row["date"],
    }


@router.get("")
def charts(
    request: Request,
    ticker: str,
    market: str,
    exchange: str | None = None,
    days: int = Query(120, ge=1, le=1000),
) -> dict:
    """`{ticker, market, candles: [{time: 날짜, open…volume}], markers: [{time, kind: "fill", side,
    text}], lines: {stop_loss, take_profit, date}}`."""
    deps, client, ex = _validate(request, ticker, market, exchange)
    key = (market, ticker, ex, days, deps.now().astimezone(KST).date().isoformat())
    candles = _hit(_candle_cache, key, CACHE_TTL_S)
    if candles is None:
        raw = _fetch(
            lambda: client.get_daily_kr(ticker, days=days)
            if market == "KR"
            else client.get_daily_us(ticker, ex, days=days)
        )
        candles = [_bar(c, c.date.isoformat()) for c in raw]
        _remember(_candle_cache, key, candles, ttl=CACHE_TTL_S)
    tz = MARKET_TZ[market]
    lo = day_bounds(date.fromisoformat(candles[0]["time"]), market)[0] if candles else None
    with closing(deps.conn_factory()) as conn:
        markers = []
        for r in _fills(conn, market, ticker, lo):
            day = datetime.fromisoformat(r["filled_at"]).astimezone(tz).date().isoformat()
            text = f"{r['account_id']} {r['side']} {r['qty']}주 @{r['price']}"
            markers.append({"time": day, "kind": "fill", "side": r["side"], "text": text})
        lines = _lines(conn, market, ticker)
    return {"ticker": ticker, "market": market, "candles": candles, "markers": markers,
            "lines": lines}  # fmt: skip


def _epoch(d: date, hhmmss: str, tz: ZoneInfo) -> int:
    t = datetime.strptime(hhmmss.ljust(6, "0"), "%H%M%S").time()
    return int(datetime.combine(d, t, tzinfo=tz).timestamp())


def _minute_key_lock(key: tuple) -> threading.Lock:
    with _cache_lock:
        idle = [k for k, lk in _minute_locks.items() if k not in _minute_cache and not lk.locked()]
        for k in idle:
            if k != key:  # 쓰지 않는 키의 락은 지운다(캐시처럼 키가 계속 늘지 않게)
                del _minute_locks[k]
        return _minute_locks.setdefault(key, threading.Lock())


def _minute_fetch(client, market, ticker, ex, count, tz, key) -> list[dict]:
    """분봉 캐시 미스: 키별 락 안에서 캐시를 다시 보고, 그래도 없을 때만 KIS 를 부른다."""
    with _minute_key_lock(key):
        hit = _hit(_minute_cache, key, MINUTE_CACHE_TTL_S)
        if hit is not None:
            return hit
        raw = _fetch(
            lambda: client.get_minute_kr(ticker, count=count)
            if market == "KR"
            else client.get_minute_us(ticker, ex, count=min(count, US_MINUTE_MAX))
        )
        out = [
            {**_bar(c, _epoch(c.date, c.time or "000000", tz)), "date": c.date.isoformat()}
            for c in raw
        ]
        _remember(_minute_cache, key, out, ttl=MINUTE_CACHE_TTL_S)
    return out


@router.get("/minute")
def minute_chart(
    request: Request,
    ticker: str,
    market: str,
    exchange: str | None = None,
    count: int = Query(120, ge=1, le=390),
) -> dict:
    """`{ticker, market, interval: "1m", tz, session_date, candles: [{time: epoch…}], markers}`.
    마커는 session_date(시장 현지) 하루의 체결을 분 단위로 내린 epoch(텍스트에 현지 HH:MM)."""
    deps, client, ex = _validate(request, ticker, market, exchange)
    tz = MARKET_TZ[market]
    key = (market, ticker, ex, count)
    candles = _hit(_minute_cache, key, MINUTE_CACHE_TTL_S)
    if candles is None:
        candles = _minute_fetch(client, market, ticker, ex, count, tz, key)
    session_date = candles[-1]["date"] if candles else None
    markers: list[dict] = []
    if session_date:
        lo, hi = day_bounds(date.fromisoformat(session_date), market)
        with closing(deps.conn_factory()) as conn:
            rows = _fills(conn, market, ticker, lo, hi)
        for r in rows:
            dt = datetime.fromisoformat(r["filled_at"]).astimezone(tz)
            if dt.date().isoformat() != session_date:
                continue
            text = f"{dt:%H:%M} {r['account_id']} {r['side']} {r['qty']}주 @{r['price']}"
            markers.append({"time": int(dt.timestamp()) // 60 * 60, "kind": "fill",
                            "side": r["side"], "text": text})  # fmt: skip
    return {
        "ticker": ticker,
        "market": market,
        "interval": "1m",
        "tz": str(tz.key),
        "session_date": session_date,
        "candles": [{k: v for k, v in c.items() if k != "date"} for c in candles],
        "markers": markers,
    }
