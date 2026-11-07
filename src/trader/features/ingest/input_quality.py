"""Small source-quality contexts shared by stock, market and scout inputs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def macro_context(macro: dict, *, asof: datetime) -> dict:
    """Observation date is not retrieval freshness or publication/release time."""
    out = {}
    for key, section in macro.items():
        if not isinstance(section, dict) or "data" not in section:
            out[key] = section
            continue
        data = section.get("data")
        observation = (data.get("date") or data.get("asof")) if isinstance(data, dict) else None
        source = "ECOS" if key == "base_rate_kr" else "KIS" if key == "kospi" else "FRED"
        future = bool(
            observation and str(observation)[:10] > asof.astimezone(UTC).date().isoformat()
        )
        gaps = []
        if section.get("error"):
            gaps.append(str(section["error"]))
        if future:
            gaps.append("observation is after asof")
        if data is not None:
            gaps.append(
                "release time/frequency freshness unavailable; observation date is not fetch time"
            )
        out[key] = {
            **section,
            "data": None if future else data,
            "source": source,
            "source_asof": observation,
            "status": "unavailable" if future or data is None else "available",
            "stale": None,
            "data_gaps": gaps,
        }
    return out


def activity_context(
    conn: Any, market: str, *, asof: datetime, port: Any = None, ticker: str | None = None
) -> dict:
    """Read bounded KIS rankings; failures remain explicit model-facing data gaps."""
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
    "wti": ("DCOILWTICO", "WTI crude oil spot price", False),
    "dxy": ("DTWEXBGS", "Federal Reserve broad US dollar index", False),
    "us10y": ("DGS10", "US Treasury 10-year constant maturity yield", True),
}


def source_coverage(
    macro: dict,
    *,
    community: dict | None = None,
    blog_posts: list[dict] | None = None,
    blog_config: list[str] | None = None,
) -> dict:
    """Requested sources are explicit: FRED proxies never become Investing.com data."""
    coverage = {}
    for key, (series, instrument, exact) in _REQUESTED_MACRO.items():
        section = macro.get(key) or {}
        available = isinstance(section, dict) and section.get("data") is not None
        coverage[key] = {
            "status": "present" if available else "missing",
            "source": "FRED",
            "requested_source": "Investing.com",
            "series_id": series,
            "instrument": instrument,
            "exact_requested_instrument": exact,
            "received_at": section.get("received_at"),
            "source_asof": section.get("source_asof"),
            "stale": section.get("stale"),
            "data_gaps": ["Investing.com source not collected; FRED series used"]
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
