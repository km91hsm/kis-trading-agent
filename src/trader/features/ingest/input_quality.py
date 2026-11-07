"""종목·시장·발굴 입력이 공유하는 원천·관측시각·품질 요약."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trader.features.ingest.macro import MACRO_INSTRUMENTS


def macro_context(macro: dict, *, asof: datetime) -> dict:
    """관측일과 조회시각·발표시각을 구분하고 asof 뒤 관측값을 차단한다."""
    out = {}
    for key, section in macro.items():
        if not isinstance(section, dict) or "data" not in section:
            out[key] = section
            continue
        data = section.get("data")
        observation = (
            ((data.get("asof") or data.get("date")) if isinstance(data, dict) else None)
            or section.get("observed_at")
            or section.get("observed_date")
        )
        source = section.get("source") or (
            "ECOS" if key == "base_rate_kr" else "KIS" if key == "kospi" else "FRED"
        )
        future = False
        timestamp_error = None
        if observation:
            raw = str(observation)
            if "T" in raw or " " in raw:
                try:
                    observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if observed.tzinfo is None:
                        raise ValueError("observation timestamp timezone unavailable")
                    future = observed.astimezone(UTC) > asof.astimezone(UTC)
                except ValueError as exc:
                    timestamp_error = str(exc)
            else:
                # Date/month observations have no release timestamp. Preserve their
                # precision and unknown release freshness instead of inventing a time.
                future = raw[:10] > asof.astimezone(UTC).date().isoformat()
        gaps = list(section.get("data_gaps") or [])
        if timestamp_error:
            gaps.append(timestamp_error)
        if section.get("error"):
            gaps.append(str(section["error"]))
        if future:
            gaps.append("observation is after asof")
        if section.get("fallback_reason"):
            gaps.append(str(section["fallback_reason"]))
        if data is not None:
            gaps.append(
                "release time/frequency freshness unavailable; observation date is not fetch time"
            )
        out[key] = {
            **section,
            "data": None if future or timestamp_error else data,
            "source": source,
            "source_asof": observation,
            "status": "unavailable" if future or timestamp_error or data is None else "available",
            "stale": section.get("stale"),
            "data_gaps": list(dict.fromkeys(gaps)),
        }
    return out


def activity_context(
    conn: Any, market: str, *, asof: datetime, port: Any = None, ticker: str | None = None
) -> dict:
    """수집 범위가 제한된 KIS 순위를 읽고 실패를 모델이 확인할 data_gaps에 남긴다."""
    try:
        if conn is None:
            raise ValueError("no activity store connection")
        if port is None:
            from trader.features.quotes.api import activity_snapshot

            snap = activity_snapshot(conn, market, asof=asof)
        else:
            snap = port.snapshot(conn, market, asof=asof)
        if snap.get("market") != market:
            raise ValueError("activity snapshot market mismatch")
        stamp = snap.get("received_at")
        if stamp and datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) > asof:
            raise ValueError("activity snapshot received after asof")
    except Exception as exc:  # noqa: BLE001 - optional input; expose gap, never fabricate zero
        return {
            "source": "kis",
            "market": market,
            "currency": "KRW" if market == "KR" else "USD",
            "received_at": None,
            "source_asof": None,
            "basis": "unknown",
            "status": "unavailable",
            "stale": None,
            "coverage": {},
            "rankings": {k: [] for k in ("turnover", "gain", "loss")},
            "data_gaps": [f"market_activity: {type(exc).__name__}: {exc}"],
        }
    result = {
        k: snap.get(k)
        for k in (
            "source",
            "market",
            "currency",
            "received_at",
            "source_asof",
            "basis",
            "status",
            "stale",
            "coverage",
            "errors",
        )
    }
    rankings = snap.get("rankings") or {}
    result["rankings"] = {
        kind: [
            r
            for r in rankings.get(kind, [])
            if ticker is None or str(r.get("ticker", "")).upper() == ticker.upper()
        ][:12]
        for kind in ("turnover", "gain", "loss")
    }
    gaps = list(snap.get("data_gaps") or [])
    if snap.get("status") != "ok":
        gaps.append(f"market_activity status: {snap.get('status')}")
    if snap.get("stale"):
        gaps.append("market_activity stale")
    if snap.get("source_asof") is None:
        gaps.append(
            "market_activity source time/session basis unknown; received_at is polling time"
        )
    if ticker and not any(result["rankings"].values()):
        gaps.append(
            "ticker absent from bounded collected rankings; does not prove no trading activity"
        )
    result["data_gaps"] = gaps
    result["context_scope"] = "ticker_matches" if ticker else "top_12_per_ranking"
    return result


_REQUESTED_MACRO = {
    "sp500": ("SP500", "S&P 500", True),
    "nasdaq": ("NASDAQCOM", "NASDAQ Composite", True),
    "wti": ("DCOILWTICO", "WTI crude oil futures", False),
    "dxy": ("DTWEXBGS", "ICE US Dollar Index (DXY)", False),
    "us10y": ("DGS10", "US 10-year Treasury yield", False),
}


def source_coverage(
    macro: dict,
    *,
    community: dict | None = None,
    blog_posts: list[dict] | None = None,
    blog_config: list[str] | None = None,
) -> dict:
    """요청 지표와 실제 선택 원천을 구분하고 일별 대체 관측의 한계를 표시한다."""
    coverage = {}
    for key, (series, requested, exact) in _REQUESTED_MACRO.items():
        section = macro.get(key) or {}
        available = isinstance(section, dict) and section.get("data") is not None
        instrument, unit, basis = MACRO_INSTRUMENTS[key]
        gaps = list(section.get("data_gaps") or [])
        if section.get("fallback_reason"):
            gaps.append(str(section["fallback_reason"]))
        coverage[key] = {
            "status": "present" if available else "missing",
            "source": section.get("source", "FRED"),
            "requested_source": requested,
            "series_id": section.get("series_id", series),
            "instrument": section.get("instrument", instrument),
            "basis": section.get("basis", basis),
            "unit": section.get("unit", unit),
            "observed_date": section.get("observed_date"),
            "observed_at": section.get("observed_at"),
            "timestamp_precision": section.get("timestamp_precision", "day"),
            "session": section.get("session", "daily_observation"),
            "live_verified": section.get("live_verified", False),
            "fallback_reason": section.get("fallback_reason"),
            "exact_requested_instrument": exact,
            "received_at": section.get("received_at"),
            "source_asof": section.get("source_asof"),
            "stale": section.get("stale"),
            "data_gaps": list(dict.fromkeys(gaps))
            + ([] if exact else ["proxy differs from requested instrument; do not equate values"])
            + ([] if available else ["no observation supplied to this input"]),
        }
    community = community or {}
    posts = [*(blog_posts or []), *(community.get("posts") or [])]
    hits = [p for p in posts if p.get("source") == "naver_blog/ranto28"]
    blog_status = (community.get("sources") or {}).get("naver_blog/ranto28")
    return {
        "macro": coverage,
        "naver_blog/ranto28": {
            "source": "Naver blog RSS",
            "url": "https://blog.naver.com/ranto28",
            "configured": "ranto28" in blog_config if blog_config is not None else None,
            "status": "present" if hits else "missing",
            "items": len(hits),
            "collection_status": blog_status,
            "source_asof": max(
                (str(p.get("posted_at")) for p in hits if p.get("posted_at")), default=None
            ),
            "received_at": max(
                (str(p.get("received_at")) for p in hits if p.get("received_at")), default=None
            ),
            "stale": None,
            "data_gaps": [] if hits else ["ranto28 posts not supplied to this input"],
        },
    }
