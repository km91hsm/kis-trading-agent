"""`/quotes*` 웹 자원(조회 전용, 토큰 없음): 화면에 보이는 종목·지수·호가·파생 시세와 환율.

- 정규장(`market_hours.is_open`)에만 KIS 를 부르고, 같은 종목은 `QUOTE_TTL_S` 동안 캐시해 여러 탭·
  요청이 KIS 호출을 늘리지 않게 한다. 장 밖·조회 실패는 마지막 캐시 또는 최신 보유 스냅샷 현재가를
  `stale: true`(`source: "snapshot"`)로 돌려준다(폴링이 오류 배너를 반복하지 않게).
- 국내는 실시간 허브(`hub.QuoteHub`)에 틱이 있으면 그 값(`source: "ws"`), 없는 종목만 REST
  (`source: "rest"`). 허브가 붙어 있으면 `stream: true`.
- `next_poll_ms`: 국내 정규장 1000 · 미국 정규장 `settings.quotes.us_poll_ms` · 파생 정규장
  `settings.quotes.fo_poll_ms` · 장외 60000.
- 시세 클라이언트는 시크릿의 공용 실전 키(`KIS_REAL_*`)로 직접 만든다(accounts 를 import 하지
  않는다 — accounts 가 quotes 를 import 한다). 키가 없으면 시세 클라이언트·허브 없이 빈 값·스냅샷만.
  런타임(시세 클라이언트 + 허브)은 원장 위치(`deps.state`)마다 프로세스에 한 번 만든다. 시세
  클라이언트가 생기면 같은 때 KIS 환율 원천(`fx.kis_fx`)도 그 클라이언트로 붙인다.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from trader.features.ingest import api as ingest
from trader.features.quotes import fx
from trader.features.quotes.hub import QuoteHub, session_busy
from trader.kernel.broker import factory, fo_codes
from trader.kernel.broker.ws import INDEX_CODES
from trader.kernel.config import market_hours
from trader.kernel.config.secrets import load_secrets

logger = logging.getLogger("trader.quotes")

router = APIRouter(tags=["quotes"])
ROUTERS = (router,)

QUOTE_TTL_S = 5.0
# 이보다 오래된 캐시 값은 지운다(장 밖 대체 값은 스냅샷이 맡는다) — 본 종목 수만큼 계속 늘지 않게
KEEP_S = 600.0
MAX_TICKERS = 20
KR_POLL_MS = 1000
OFF_POLL_MS = 60000
US_EXCHANGES = ("NAS", "NYS", "AMS")
_TICKER_RE = re.compile(r"^(?!\.+$)[A-Za-z0-9.\-]{1,16}$")  # 점만 있는 값(., ..)은 거부
_FO_CODE_RE = re.compile(r"^[A-Z0-9]{6,12}$")

_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_lock = threading.Lock()
_key_locks: dict[tuple[str, str], threading.Lock] = {}  # 같은 종목 동시 조회를 한 번의 KIS 호출로


# --- 런타임(시세 클라이언트 + 허브), 프로세스에 한 번 -----------------------------


@dataclass
class Runtime:
    market: Any | None
    hub: Any | None


_runtimes: dict[Path, Runtime] = {}
_runtime_lock = threading.Lock()


def _build_runtime(deps: Any) -> Runtime:
    try:
        creds = load_secrets().kis_creds(env="real")
    except Exception as e:  # noqa: BLE001 — 시크릿 오류는 시세 없음으로
        logger.warning("시세 클라이언트 생략: 시크릿 읽기 실패 (%s)", type(e).__name__)
        return Runtime(None, None)
    if not creds.app_key or not creds.app_secret:
        logger.info("시세 클라이언트 없음: 공용 실전 키(KIS_REAL_*)가 설정되지 않았습니다")
        return Runtime(None, None)
    try:
        market = factory.build_market(creds)
        hub = None
        if deps.settings.quotes.realtime_ws:
            hub = QuoteHub.for_market(
                market, now=deps.now, busy=session_busy(deps.conn_factory, deps.now)
            )
    except Exception as e:  # noqa: BLE001 — 조립 실패는 시세 없음으로(요청이 500 이 되지 않게)
        logger.warning("시세 클라이언트·허브 생략: 조립 실패 (%s)", type(e).__name__)
        return Runtime(None, None)
    fx.kis_fx = _kis_fx_source(market, deps.settings.quotes.fx_code)
    return Runtime(market, hub)


def _kis_fx_source(market: Any, code: str):
    """`fx.kis_fx` 콜러블: `get_fx_usdkrw(code)`(FHKST03030100) → `get_fx_from_detail()`
    (HHDFS76200200 `t_rate`) 순서로 처음 값 `{rate, as_of, code}`, 둘 다 없거나 실패하면 None
    (다음 원천 FRED 는 `fx.rate` 가 잇는다)."""

    def source() -> dict | None:
        for name, step in (
            ("FHKST03030100", lambda: market.get_fx_usdkrw(code)),
            ("HHDFS76200200", market.get_fx_from_detail),
        ):
            try:
                got = step()
            except Exception as e:  # noqa: BLE001 — 한 TR 실패는 다음 TR 로
                logger.warning("KIS 환율 %s 실패: %s", name, type(e).__name__)
                continue
            if got and got.get("rate"):
                return got
        return None

    return source


def runtime_for(deps: Any) -> Runtime:
    """원장 위치마다 한 번 만든 런타임(요청마다 만들지 않는다)."""
    with _runtime_lock:
        rt = _runtimes.get(deps.state)
        if rt is None:
            rt = _runtimes[deps.state] = _build_runtime(deps)
        return rt


def install(state: Path, *, market: Any | None, hub: Any | None = None) -> None:
    """런타임을 직접 끼운다(테스트·조립용)."""
    with _runtime_lock:
        _runtimes[state] = Runtime(market, hub)


def reset() -> None:
    """런타임(허브는 멈춘다)과 REST 캐시를 비운다."""
    with _runtime_lock:
        olds = list(_runtimes.values())
        _runtimes.clear()
    fx.kis_fx = None
    for rt in olds:
        if rt.hub is not None:
            rt.hub.stop()
    with _lock:
        _cache.clear()
        _key_locks.clear()


# --- REST 캐시 -------------------------------------------------------------------


def _key_lock(key: tuple[str, str]) -> threading.Lock:
    with _lock:
        return _key_locks.setdefault(key, threading.Lock())


def _fresh(key: tuple[str, str]) -> tuple[float, dict] | None:
    with _lock:
        return _cache.get(key)


def _remember(key: tuple[str, str], val: dict, *, now: float | None = None) -> None:
    """저장하면서 KEEP_S 지난 값과 쓰지 않는 종목 락을 지운다."""
    t = time.monotonic() if now is None else now
    with _lock:
        for k in [k for k, (saved, _) in _cache.items() if t - saved >= KEEP_S]:
            del _cache[k]
        _cache[key] = (t, val)
        for k in [k for k, lk in _key_locks.items() if k not in _cache and not lk.locked()]:
            del _key_locks[k]


def _cached(key: tuple[str, str], fetch: Any) -> dict | None:
    """TTL 안이면 캐시, 아니면 종목별 락 안에서 다시 보고 한 번만 조회한다. 실패하면 마지막 값을
    stale 로(없으면 None)."""
    hit = _fresh(key)
    if hit and time.monotonic() - hit[0] < QUOTE_TTL_S:
        return hit[1]
    # 종목별 락 안에서 캐시를 다시 본다: 먼저 온 요청이 받은 값을 뒤 요청이 쓴다
    with _key_lock(key):
        hit = _fresh(key)
        if hit and time.monotonic() - hit[0] < QUOTE_TTL_S:
            return hit[1]
        try:
            val = fetch()
        except Exception as e:  # noqa: BLE001 - 바쁨·KIS 오류는 마지막 값으로
            logger.info("시세 조회 실패 %s: %s", key, type(e).__name__)
            return {**hit[1], "stale": True} if hit else None
        _remember(key, val)
        return val


def _snapshot_prices(conn, market: str, tickers: list[str]) -> dict[str, dict]:
    """계좌 무관 최신 현물 스냅샷 현재가(종목별 가장 최근 날짜). 종목 수만큼 쿼리하지 않고
    한 번에 읽는다."""
    out: dict[str, dict] = {}
    want = list(dict.fromkeys(tickers))
    if not want:
        return out
    qs = ",".join("?" * len(want))
    rows = conn.execute(
        "SELECT ticker, current_price, date FROM positions_snapshot WHERE market=? "
        f"AND asset='stock' AND ticker IN ({qs}) AND current_price IS NOT NULL "
        "ORDER BY ticker, date DESC, id DESC",
        (market, *want),
    ).fetchall()
    for r in rows:
        if r[0] not in out:
            out[r[0]] = {
                "price": r[1],
                "change_pct": None,
                "at": r[2],
                "stale": True,
                "source": "snapshot",
            }
    return out


def _us_exchanges() -> dict[str, str]:
    """유니버스의 US 거래소 맵. 접미 없는 US 종목의 거래소는 여기서 찾는다(없으면 NAS)."""
    try:
        return {s.symbol: s.exchange for s in ingest.load_universe().us}
    except Exception:  # noqa: BLE001 - 유니버스가 없으면 NAS
        return {}


def _parse(market: str, tickers: str) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for raw in (x.strip() for x in tickers.split(",")):
        if not raw:
            continue
        t, _, ex = raw.partition(":")
        if not _TICKER_RE.fullmatch(t):
            raise HTTPException(status_code=400, detail=f"ticker 형식 오류: {t!r}")
        if ex and (market != "US" or ex not in US_EXCHANGES):
            raise HTTPException(status_code=400, detail=f"거래소 {ex} 는 US 의 NAS|NYS|AMS 만")
        out.append((t, ex or None))
    if len(out) > MAX_TICKERS:
        raise HTTPException(status_code=400, detail=f"한 번에 {MAX_TICKERS}종목까지")
    return out


def _quote_val(q: Any) -> dict:
    return {
        "price": str(q.price),
        "change_pct": q.change_pct,
        "at": q.asof.isoformat(),
        "stale": False,
        "source": "rest",
    }


# --- 자원 -------------------------------------------------------------------------


@router.get("/quotes")
def quotes(request: Request, market: str, tickers: str = "") -> dict:
    if market not in ("KR", "US"):
        raise HTTPException(status_code=400, detail="market 은 KR|US")
    deps = request.app.state.deps
    items = _parse(market, tickers)
    now = deps.now()
    is_open = market_hours.is_open(market, now)
    rt = runtime_for(deps)
    out: dict[str, dict] = {}
    missing: list[str] = []
    hub = rt.hub if market == "KR" and is_open else None
    if hub is not None:
        out.update(hub.request([t for t, _ in items]))
        items = [(t, ex) for t, ex in items if t not in out]
    if is_open and rt.market is not None:
        m = rt.market
        us_ex = _us_exchanges() if market == "US" and any(ex is None for _, ex in items) else {}
        for t, ex in items:
            if market == "KR":
                fetch = lambda t=t: _quote_val(m.get_price_kr(t))  # noqa: E731
            else:
                exch = ex or us_ex.get(t, "NAS")
                fetch = lambda t=t, exch=exch: _quote_val(m.get_price_us(t, exch))  # noqa: E731
            val = _cached((market, t), fetch)
            if val is None:
                missing.append(t)
            else:
                out[t] = val
    else:
        missing = [t for t, _ in items]
    if missing:
        with closing(deps.conn_factory()) as conn:
            out.update(_snapshot_prices(conn, market, missing))
    status = hub.status() if hub is not None else {}
    stream = bool(status.get("connected"))
    if not is_open:
        poll = OFF_POLL_MS
    else:
        poll = KR_POLL_MS if market == "KR" else deps.settings.quotes.us_poll_ms
    return {
        "session": "open" if is_open else "closed",
        "at": now.isoformat(),
        "quotes": out,
        "source": "ws" if stream else "rest",
        "stream": stream,
        # 허브가 붙지 않는 이유: 체결통보 리스너가 앱키 세션을 쓰는 중이면 ws_session_busy
        "hub_yield": status.get("yield_reason"),
        "next_poll_ms": poll,
    }


@router.get("/quotes/indices")
def indices(request: Request) -> dict:
    """KOSPI·KOSDAQ 지수(상단 바). 허브 틱 먼저, 없는 지수는 REST. 장 밖·시세 없음이면 빈 값."""
    deps = request.app.state.deps
    now = deps.now()
    is_open = market_hours.is_open("KR", now)
    rt = runtime_for(deps)
    got: dict[str, dict] = {}
    if is_open:
        if rt.hub is not None:
            got.update(rt.hub.request_indices())
        if rt.market is not None:
            m = rt.market
            for code, name in INDEX_CODES.items():
                if name in got:
                    continue
                val = _cached(("IDX", code), lambda code=code: _quote_val(m.get_index_kr(code)))
                if val is not None:
                    got[name] = val
    return {
        "session": "open" if is_open else "closed",
        "at": now.isoformat(),
        "indices": got,
        "next_poll_ms": KR_POLL_MS if is_open else OFF_POLL_MS,
    }


def _book_val(b: Any) -> dict:
    return {
        "asks": [[str(p), str(q)] for p, q in b.asks],
        "bids": [[str(p), str(q)] for p, q in b.bids],
        "total_ask": None,
        "total_bid": None,
        "time": None,
        "at": b.asof.isoformat(),
        "source": "rest",
    }


@router.get("/quotes/book")
def book(request: Request, ticker: str) -> dict:
    """국내 종목 호가 10단(차트 화면). 허브(임차 10초) 먼저, 없으면 REST. 표시 전용."""
    deps = request.app.state.deps
    if not _TICKER_RE.fullmatch(ticker):
        raise HTTPException(status_code=400, detail=f"ticker 형식 오류: {ticker!r}")
    now = deps.now()
    is_open = market_hours.is_open("KR", now)
    rt = runtime_for(deps)
    got = None
    if is_open:
        if rt.hub is not None:
            got = rt.hub.request_book(ticker)
        if got is None and rt.market is not None:
            m = rt.market
            got = _cached(("BOOK", ticker), lambda: _book_val(m.get_orderbook_kr(ticker)))
    return {
        "session": "open" if is_open else "closed",
        "ticker": ticker,
        "book": got,
        "next_poll_ms": KR_POLL_MS if is_open else OFF_POLL_MS,
    }


def _fo_val(q: Any) -> dict:
    at = q.quote_asof or q.received_at
    return {
        "kind": q.kind,
        "price": str(q.price),
        "change_pct": q.change_pct,
        "bid": None if q.bid is None else str(q.bid),
        "ask": None if q.ask is None else str(q.ask),
        "at": at.isoformat(),
        "stale": False,
        "source": "rest",
    }


def _kospi200_val(m: Any) -> dict:
    price, at = m.get_kospi200()
    return {"price": str(price), "at": None if at is None else at.isoformat(), "source": "rest"}


@router.get("/quotes/fo")
def fo(request: Request, codes: str = "") -> dict:
    """선물옵션 시세(REST 만). 종류는 `settings.fo` 상품 접두로 판정, 모르는 코드는 400."""
    deps = request.app.state.deps
    product = deps.settings.fo.product_cfg()
    items: list[tuple[str, str]] = []
    for raw in (x.strip().upper() for x in codes.split(",")):
        if not raw:
            continue
        kind = fo_codes.kind_of(raw, product) if _FO_CODE_RE.fullmatch(raw) else None
        if kind is None:
            detail = f"{product.name} 종목코드가 아닙니다: {raw}"
            raise HTTPException(status_code=400, detail=detail)
        items.append((raw, kind))
    if len(items) > MAX_TICKERS:
        raise HTTPException(status_code=400, detail=f"한 번에 {MAX_TICKERS}종목까지")
    now = deps.now()
    is_open = market_hours.is_open("KR", now)
    rt = runtime_for(deps)
    out: dict[str, dict] = {}
    kospi200 = None
    if is_open and rt.market is not None:
        m = rt.market
        for code, kind in items:
            val = _cached(("FO", code), lambda c=code, k=kind: _fo_val(m.get_fo_price(c, k)))
            if val is not None:
                out[code] = val
        kospi200 = _cached(("IDX", "KOSPI200"), lambda: _kospi200_val(m))
    return {
        "session": "open" if is_open else "closed",
        "at": now.isoformat(),
        "quotes": out,
        "kospi200": kospi200,
        "next_poll_ms": deps.settings.quotes.fo_poll_ms if is_open else OFF_POLL_MS,
    }


@router.get("/quotes/fx")
def fx_quote(request: Request, pair: str = "USDKRW") -> dict:
    """환율 `{pair, rate, as_of, source: "kis"|"macro"}`. KST 하루 1회 캐시, 원천이 없으면 404."""
    if pair not in fx.PAIRS:
        raise HTTPException(status_code=400, detail=f"pair 는 {'|'.join(fx.PAIRS)} 만")
    deps = request.app.state.deps
    runtime_for(deps)  # KIS 환율 원천(`fx.kis_fx`)은 런타임 조립 때 붙는다
    with closing(deps.conn_factory()) as conn:
        got = fx.rate(conn, pair, now=deps.now())
    if got is None:
        detail = f"{pair} 환율 원천이 없습니다(KIS·거시 모두 실패)"
        raise HTTPException(status_code=404, detail=detail)
    return got


@router.get("/quotes/activity")
def activity(request: Request, market: str = "KR") -> dict:
    """KIS 시장 활동 저장 이력만 읽는다. 화면 새로고침은 브로커 호출을 만들지 않는다."""
    from trader.features.quotes.market_activity import snapshot

    if market not in {"KR", "US"}:
        raise HTTPException(400, "market은 KR 또는 US여야 합니다")
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        result = snapshot(conn, market, asof=deps.now())
    result["enabled"] = deps.settings.quotes.activity_enabled
    if not result["enabled"]:
        result["collection_state"] = "disabled"
    return result
