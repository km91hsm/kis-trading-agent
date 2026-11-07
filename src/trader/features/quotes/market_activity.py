"""저장된 KIS 순위와 누적 거래대금의 관측 변화. 네트워크 호출은 수집 잡만 한다."""

from __future__ import annotations

import copy
import math
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal

from trader.features.quotes import activity_store
from trader.kernel.broker.market_activity import finite_decimal

KINDS = ("turnover", "gain", "loss")


def _unavailable(reason: str) -> dict:
    return dict(
        status="unavailable",
        value=None,
        recent_5m=None,
        previous_5m=None,
        basis="observed_api_cumulative_turnover",
        reason=reason,
    )


def _identity(row: dict) -> tuple:
    return tuple(
        row.get(k)
        for k in ("market", "ticker", "exchange", "currency", "observed_day", "observation_session")
    )


def _observations(history: list) -> dict:
    indexed = {}
    for batch_at, partitions in history:
        for part in partitions.values():
            at = (
                datetime.fromisoformat(part["received_at"]) if part.get("received_at") else batch_at
            )
            for row in part.get("rows", []):
                indexed.setdefault(_identity(row), {})[at] = finite_decimal(row.get("turnover"))
    return indexed


def _growth(row: dict, received_at: str, indexed: dict, *, stale: bool) -> dict:
    if stale:
        return _unavailable("stale_observation")
    if not row.get("observed_day") or not row.get("observation_session"):
        return _unavailable("observation_session_unknown")
    end = datetime.fromisoformat(received_at)
    observed = {at: value for at, value in indexed.get(_identity(row), {}).items() if at <= end}
    current = finite_decimal(row.get("turnover"))
    observed[end] = current
    selected = []
    times = []
    for minutes in (10, 5, 0):
        target = end - timedelta(minutes=minutes)
        choices = [
            (abs((t - target).total_seconds()), t)
            for t in observed
            if abs((t - target).total_seconds()) <= 60
        ]
        if not choices:
            return _unavailable("three_boundaries_not_ready")
        _, t = min(choices)
        times.append(t)
        selected.append(observed[t])
    if not times[0] < times[1] < times[2] or any(x is None for x in selected):
        return _unavailable("missing_counter")
    interval = [value for t, value in sorted(observed.items()) if times[0] <= t <= end]
    last = None
    for value in interval:
        if value is None:
            continue
        if value < 0 or (last is not None and value < last):
            return _unavailable("counter_reset_or_negative_delta")
        last = value
    old, middle, new = selected
    previous, recent = middle - old, new - middle
    if previous <= 0 or recent < 0:
        return _unavailable("zero_denominator_or_negative_delta")
    value = float(recent / previous - 1)
    if not math.isfinite(value):
        return _unavailable("nonfinite_growth")
    return dict(
        status="available",
        value=value,
        recent_5m=str(recent),
        previous_5m=str(previous),
        basis="observed_api_cumulative_turnover",
        reason=None,
    )


def snapshot(
    conn: sqlite3.Connection, market: str, *, asof: datetime, max_age_s: int = 120
) -> dict:
    if market not in {"KR", "US"}:
        raise ValueError("market은 KR 또는 US여야 합니다")
    at = activity_store.utc(asof)
    venues = ["KRX"] if market == "KR" else ["NAS", "NYS", "AMS"]
    out = dict(
        source="kis",
        market=market,
        currency="KRW" if market == "KR" else "USD",
        received_at=None,
        source_asof=None,
        basis="unknown",
        status="unavailable",
        stale=True,
        coverage=dict(
            expected=venues,
            successful=[],
            failed=[],
            scope="bounded_rankings",
            complete=False,
            session="unknown",
            partitions={},
        ),
        rankings={k: [] for k in KINDS},
        errors=[],
        data_gaps=["source_asof_unknown", "source_session_unknown", "bounded_ranking_coverage"],
    )
    observations = _observations(activity_store.history(conn, market, asof=at))
    stale_rows = []
    for venue in venues:
        for kind in KINDS:
            label = f"{venue}:{kind}"
            newest = activity_store.latest(conn, market, label, asof=at)
            success = activity_store.latest(conn, market, label, asof=at, success=True)
            detail = dict(
                pages=0,
                truncated=False,
                max_pages=None,
                limit=None,
                received_at=None,
                status="missing",
                rows=0,
            )
            out["coverage"]["partitions"][label] = detail
            if newest:
                stamp, p = newest
                detail.update(
                    pages=p.get("pages", 0),
                    truncated=bool(p.get("truncated")),
                    max_pages=p.get("max_pages"),
                    limit=p.get("limit"),
                    received_at=p.get("received_at") or stamp,
                    status="error" if p.get("error") or not p.get("ok") else "ok",
                    rows=len(p.get("rows", [])),
                )
                out["received_at"] = max(out["received_at"] or stamp, stamp)
                if p.get("error"):
                    out["errors"].append(
                        dict(exchange=venue, kind=kind, error=p["error"], received_at=stamp)
                    )
            if not success:
                out["coverage"]["failed"].append(label)
                continue
            stamp, p = success
            stamp = p.get("received_at") or stamp
            failed = bool(newest and (not newest[1].get("ok") or newest[1].get("error")))
            stale = (at - datetime.fromisoformat(stamp)).total_seconds() > max_age_s or failed
            out["coverage"]["successful"].append(label)
            if failed:
                out["coverage"]["failed"].append(label)
            for raw in p.get("rows", []):
                if (raw.get("market"), raw.get("exchange"), raw.get("currency")) != (
                    market,
                    venue,
                    out["currency"],
                ):
                    continue
                row = copy.deepcopy(raw)
                row.update(
                    received_at=stamp,
                    stale=stale,
                    source_asof=raw.get("source_asof"),
                    basis=raw.get("basis", "unknown"),
                )
                row["growth"] = _growth(row, stamp, observations, stale=stale)
                stale_rows.append(stale)
                out["rankings"][kind].append(row)
    for kind, rows in out["rankings"].items():
        key = "turnover" if kind == "turnover" else "change_pct"

        # Exact decimal ordering within one market/currency; absent values sort last.
        def order(row, key=key, kind=kind):
            value = finite_decimal(row.get(key))
            return (
                value is None,
                (value if kind == "loss" else -value) if value is not None else Decimal(0),
            )

        rows.sort(key=order)
    if out["coverage"]["successful"]:
        out["status"] = "partial"  # bounded rankings never prove the entire market
        out["stale"] = any(stale_rows) if stale_rows else True
    return out


def for_ticker(snapshot: dict, ticker: str) -> dict:
    out = {k: copy.deepcopy(v) for k, v in snapshot.items() if k != "rankings"}
    out["ticker"] = ticker
    out["rankings"] = {
        kind: [
            copy.deepcopy(row)
            for row in snapshot.get("rankings", {}).get(kind, [])
            if row.get("ticker") == ticker
        ]
        for kind in KINDS
    }
    if not any(out["rankings"].values()):
        out["status"] = "unavailable"
        out.setdefault("data_gaps", []).append(
            "ticker_not_in_collected_rankings_membership_unknown"
        )
    return out
