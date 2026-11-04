"""환율 원천과 하루 캐시. 순서: KIS → 거시(FRED `DEXKOUS`, 원/달러) → 없음(None, 라우트는 404).

- KIS 원천은 주입점 `kis_fx`(기본 None)만 둔다 — KIS 의 단순 환율 시세 TR 을 확인하지 못해 추측으로
  TR 을 박지 않았다(후속 작업이 시세 클라이언트 메서드로 채운다). `() -> {"rate", "as_of"} | None`.
- 거시 원천은 `ingest.api.Fred` 로 `DEXKOUS` 최신 관측값을 읽는다(`FRED_API_KEY` 없으면 원천 없음).
- 결과는 control `fx_cache:<pair>` 에 KST 날짜와 함께 저장해 하루 한 번만 원천을 부른다.
  실패(None)는 캐시하지 않는다. 원천이 던지는 예외는 그 원천을 건너뛴다(표시 전용 — 주문·게이트는
  쓰지 않는다).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from trader.features.ingest import api as ingest
from trader.kernel.config import KST
from trader.kernel.config.secrets import load_secrets
from trader.kernel.ledger.control import get_flag, set_flag

logger = logging.getLogger("trader.quotes")

PAIRS = ("USDKRW",)
FRED_USDKRW = "DEXKOUS"  # FRED 원/달러(ingest.macro FRED_SERIES["usdkrw"] 와 같은 시리즈)
CACHE_PREFIX = "fx_cache:"

# KIS 환율 원천 주입점(후속 작업). None 이면 KIS 원천 없음
kis_fx: Callable[[], dict | None] | None = None


def macro_usdkrw(*, http_factory: Callable[[], Any] = httpx.Client) -> dict | None:
    """FRED DEXKOUS 최신 관측값 {rate: str, as_of: YYYY-MM-DD}. 키가 없으면 None(네트워크 없음)."""
    key = getattr(load_secrets(), "FRED_API_KEY", None)
    if not key:
        return None
    with http_factory() as http:
        obs = ingest.Fred(key, http).latest(FRED_USDKRW)
    return {"rate": str(Decimal(str(obs["value"]))), "as_of": str(obs["date"])}


def _try(name: str, source: Callable[[], dict | None] | None) -> dict | None:
    if source is None:
        return None
    try:
        got = source()
    except Exception as e:  # noqa: BLE001 — 한 원천 실패는 다음 원천으로
        logger.warning("환율 원천 %s 실패: %s", name, type(e).__name__)
        return None
    if not got or got.get("rate") in (None, ""):
        return None
    return {"rate": str(Decimal(str(got["rate"]))), "as_of": got.get("as_of"), "source": name}


def rate(conn: sqlite3.Connection, pair: str, *, now: datetime) -> dict | None:
    """`{pair, rate: str(Decimal), as_of, source: "kis"|"macro"}` 또는 None(모르는 쌍·원천 없음)."""
    if pair not in PAIRS:
        return None
    day = now.astimezone(KST).date().isoformat()
    key = CACHE_PREFIX + pair
    cached = get_flag(conn, key)
    if isinstance(cached, dict) and cached.get("day") == day:
        return {k: cached[k] for k in ("pair", "rate", "as_of", "source")}
    got = _try("kis", kis_fx) or _try("macro", macro_usdkrw)
    if got is None:
        return None
    out = {"pair": pair, **got}
    set_flag(conn, key, {**out, "day": day})
    return out
