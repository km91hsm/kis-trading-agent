"""성과·분석 조회 모양(웹 `/analytics/*` 전용, 읽기만). 라우터는 검증만 하고 여기서 원장을 읽는다.

- `cached_report`: 계좌·시장 성과 리포트 캐시(옛 `routers/performance.cached_report`). 리포트는 피처
  종가를 읽어 수백 ms 라 SSE 갱신마다 다시 만들지 않는다 — 입력 버전(스냅샷·NAV·체결·주문 수와
  피처 디렉터리 mtime)이 같고 `CACHE_TTL_S` 안이면 그대로. `CACHE_MAX` 개까지(LRU).
- `compare_rows`: 계좌·시장마다 `metrics_of`(NAV 이력 없으면 `metrics: None`). 레인은 asset fo → fo,
  그 외 spot(단타는 여기서 구분할 수 없다).
- `shadow_summary`·`calibration_runs`·`memory_recent`·`memory_search`."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

from trader.features.evaluation import performance, readiness, shadow
from trader.features.evaluation.memory import store
from trader.features.ml import api as ml_api
from trader.kernel.ledger import names

CACHE_TTL_S = 300.0
CACHE_MAX = 64  # 리포트 캐시 상한(LRU) — 계좌·시장·days 조합이 프로세스 수명 동안 늘지 않게
CURRENCY = {"KR": "KRW", "US": "USD"}
SHADOW_BASELINES = ("candidates_1n", "quant_topn", "index_hold", "index_trend")
# (account, market, days) → (version, saved_at, report). 최근에 쓴 키가 끝(LRU)
_CACHE: OrderedDict[tuple[str, str, int], tuple[tuple, float, dict]] = OrderedDict()
_LOCK = threading.Lock()


def _version(
    conn: sqlite3.Connection, account_id: str, market: str, state: Path | None, asset: str = "stock"
) -> tuple:
    """리포트 입력이 바뀌었는지 보는 값: 스냅샷·NAV(새 원장은 `account_nav`, 파생은
    `fo_nav_snapshot` — 값 합까지 봐 같은 날 upsert 도 잡는다)·체결·주문 수와 피처 디렉터리
    mtime."""
    if asset == "fo":
        nav = conn.execute(
            "SELECT COUNT(*), MAX(date), SUM(CAST(nav AS REAL)) FROM fo_nav_snapshot "
            "WHERE account_id=?",
            (account_id,),
        ).fetchone()
    else:
        nav = conn.execute(
            "SELECT COUNT(*), MAX(id), SUM(CAST(nav AS REAL)) FROM account_nav "
            "WHERE account_id=? AND market=?",
            (account_id, market),
        ).fetchone()
    snap = conn.execute(
        "SELECT COUNT(*) AS n, MAX(id) AS m FROM positions_snapshot "
        "WHERE account_id=? AND market=?",
        (account_id, market),
    ).fetchone()
    fills = conn.execute(
        "SELECT COUNT(*) AS n FROM fills WHERE account_id=?", (account_id,)
    ).fetchone()["n"]
    orders = conn.execute(
        "SELECT COUNT(*) AS n FROM orders WHERE account_id=?", (account_id,)
    ).fetchone()["n"]
    return (snap["n"], snap["m"], tuple(nav), fills, orders, ml_api.store_mtime(state))


def cached_report(
    conn: sqlite3.Connection,
    state: Path | None,
    account_id: str,
    market: str,
    days: int,
    *,
    asset: str = "stock",
) -> dict:
    key = (account_id, market, days)
    version = _version(conn, account_id, market, state, asset)
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == version and now - hit[1] < CACHE_TTL_S:
            _CACHE.move_to_end(key)
            return hit[2]
    report = performance.report(conn, state, account_id, market, days=days, asset=asset)
    # 리포트가 피처 디렉터리를 만들 수 있어(mtime 변화) 만든 **뒤**의 버전을 기억한다
    with _LOCK:
        _CACHE[key] = (_version(conn, account_id, market, state, asset), now, report)
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_MAX:
            _CACHE.popitem(last=False)
    return report


def compare_rows(conn: sqlite3.Connection, accounts: list[Any], *, days: int) -> list[dict]:
    """계좌·시장마다 한 행. 계좌 식별은 id 만(계좌번호 없음)."""
    out = []
    for a in accounts:
        asset = getattr(a, "asset", "stock")
        lane = "fo" if asset == "fo" else "spot"
        for m in a.markets:
            series = performance.nav_series(conn, a.id, m, days=days, asset=asset)
            out.append({
                "account_id": a.id, "env": a.env, "lane": lane, "market": m,
                "currency": CURRENCY.get(m, "KRW"),
                "metrics": performance.metrics_of(series) if series else None,
            })  # fmt: skip
    return out


def shadow_summary(conn: sqlite3.Connection, market: str) -> dict:
    """섀도 포트폴리오: LLM 선택 − 같은 날 기준선의 만기 쌍 통계 + 현금일 효과 + 최근 10일."""
    pairs = {b: shadow.paired_stats(conn, b=b, market=market) for b in SHADOW_BASELINES}
    by_date: dict[str, dict] = {}
    for r in shadow.rows(conn, market=market):
        by_date.setdefault(r["date"], {"date": r["date"]})[r["strategy"]] = r["fwd_ret"]
    recent = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)[:10]
    return {
        "market": market,
        "min_pairs": readiness.MIN_SHADOW_PAIRS,
        "pairs": pairs,
        "cash_effect": shadow.cash_effect(conn, market=market),
        "recent": recent,
    }


def calibration_runs(conn: sqlite3.Connection, *, limit: int) -> list[dict]:
    """`calibration_runs` 최근(최신 먼저). 빈 보고서(`{}`, 표본 부족)는 report None."""
    rows = conn.execute(
        "SELECT id, created_at, n_decisions, active, json FROM calibration_runs "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        try:
            report = json.loads(r["json"]) if r["json"] else None
        except ValueError:
            report = None
        out.append({
            "id": r["id"], "created_at": r["created_at"], "n_decisions": r["n_decisions"],
            "active": bool(r["active"]), "report": report or None,
        })  # fmt: skip
    return out


def _public(conn: sqlite3.Connection, entry: dict) -> dict:
    """화면용: 상황 벡터(12차원 숫자)는 빼고 종목명(원장 ticker_names, 없으면 None)을 더한다."""
    out = {k: v for k, v in entry.items() if k != "situation_vector"}
    out["name"] = names.resolve(conn, entry.get("market"), entry.get("ticker"))
    return out


def memory_recent(conn: sqlite3.Connection, *, limit: int) -> dict:
    rows = conn.execute(
        "SELECT * FROM memory_entries ORDER BY decided_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return {"query": None, "entries": [_public(conn, store._hydrate(r)) for r in rows]}


def memory_search(
    conn: sqlite3.Connection, q: str, *, limit: int, now: datetime, settings: Any
) -> dict:
    """q = 종목: 그 종목의 가장 최근 엔트리 상황(벡터·시장)으로 `store.retrieve`. 엔트리가 없으면
    빈 결과. 워밍업·브레이커면 retrieve 가 빈 목록과 그 상태를 돌려준다."""
    ticker = q.strip().upper()
    ref = conn.execute(
        "SELECT id, market, situation_vector FROM memory_entries WHERE ticker=? "
        "ORDER BY decided_at DESC, id DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if ref is None:
        return {"query": None, "entries": [], "warmup": False, "breaker": None}
    r = store.retrieve(
        conn,
        vector=json.loads(ref["situation_vector"] or "[]"),
        now=now,
        settings=settings,
        market=ref["market"],
        k=limit,
    )
    return {
        "query": {"ticker": ticker, "entry_id": ref["id"], "market": ref["market"]},
        "entries": [_public(conn, e) for e in r["entries"]],
        "warmup": r["warmup"],
        "breaker": r["breaker"],
    }
