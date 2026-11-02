"""spot 결정 저장소: 체크포인트(10_/20_/30_/31_) → 원장 테이블 멱등 적재와 조회.

원장 연결은 호출자가 소유한다. 쓰기는 호출마다 commit 한다(행 단위 멱등)."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any

from trader.kernel.ledger.db import now_iso

_ANALYST_RE = re.compile(r"^10_analyst_([a-z_]+)$")
_DEBATE_RE = re.compile(r"^20_debate_r(\d+)_(bull|bear)$")
_DECISION_RE = re.compile(r"^30_decision$")
_ROUND_RE = re.compile(r"^31_decision_r(\d+)$")  # 재평가 라운드 (M5)

_BUY_ACTIONS = ("BUY", "ADD")
_SELL_ACTIONS = ("SELL", "TRIM")


def _txt(v: Any) -> str | None:
    """금액·비중은 TEXT(Decimal 문자열)로 저장한다."""
    return None if v is None else str(Decimal(str(v)))


def _js(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def ingest_checkpoint(
    conn: sqlite3.Connection, d: date, market: str, ticker: str, stage: str, cp: dict
) -> None:
    """10_/20_/30_/31_ 체크포인트를 멱등 저장한다. 그 외 단계·`data` 없는 체크포인트는 무시."""
    data = cp.get("data") if isinstance(cp, dict) else None
    if not isinstance(data, dict):
        return
    day, now, js = d.isoformat(), now_iso(), _js(data)
    if m := _ANALYST_RE.match(stage):
        conn.execute(
            "INSERT INTO analyst_reports(decision_date, market, ticker, role, signal, score, "
            "confidence, data_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(decision_date, market, ticker, role) DO UPDATE SET "
            "signal=excluded.signal, score=excluded.score, confidence=excluded.confidence, "
            "data_json=excluded.data_json",
            (
                day,
                market,
                ticker,
                m.group(1),
                data.get("signal"),
                data.get("score"),
                data.get("confidence"),
                js,
                now,
            ),
        )
    elif m := _DEBATE_RE.match(stage):
        conn.execute(
            "INSERT INTO debate_turns(decision_date, market, ticker, round, side, stance, "
            "conviction, data_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(decision_date, market, ticker, round, side) DO UPDATE SET "
            "stance=excluded.stance, conviction=excluded.conviction, data_json=excluded.data_json",
            (
                day,
                market,
                ticker,
                int(m.group(1)),
                m.group(2),
                data.get("stance"),
                data.get("conviction"),
                js,
                now,
            ),
        )
    elif _DECISION_RE.match(stage):
        conn.execute(
            "INSERT INTO decisions(date, market, ticker, action, target_weight, order_type, "
            "limit_price, stop_loss, take_profit, horizon_days, confidence, rationale, "
            "data_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date, market, ticker) DO UPDATE SET action=excluded.action, "
            "target_weight=excluded.target_weight, order_type=excluded.order_type, "
            "limit_price=excluded.limit_price, stop_loss=excluded.stop_loss, "
            "take_profit=excluded.take_profit, horizon_days=excluded.horizon_days, "
            "confidence=excluded.confidence, rationale=excluded.rationale, "
            "data_json=excluded.data_json",
            (
                day,
                market,
                ticker,
                data.get("action"),
                _txt(data.get("target_weight")),
                data.get("order_type"),
                _txt(data.get("limit_price")),
                _txt(data.get("stop_loss")),
                _txt(data.get("take_profit")),
                data.get("horizon_days"),
                data.get("confidence"),
                data.get("rationale"),
                js,
                now,
            ),
        )
        did = decision_id(conn, d, market, ticker)
        if did is not None:
            _upsert_round(conn, did, 0, data, js, now)
    elif m := _ROUND_RE.match(stage):
        did = decision_id(conn, d, market, ticker)
        if did is not None:
            _upsert_round(conn, did, int(m.group(1)), data, js, now)
    conn.commit()


def _upsert_round(
    conn: sqlite3.Connection, did: int, rnd: int, data: dict, js: str, now: str
) -> None:
    conn.execute(
        "INSERT INTO decision_rounds(decision_id, round, action, stop_loss, source, "
        "skipped_debate, data_json, created_at) "
        "SELECT id, ?, ?, ?, source, skipped_debate, ?, ? FROM decisions WHERE id=? "
        "ON CONFLICT(decision_id, round) DO UPDATE SET action=excluded.action, "
        "stop_loss=excluded.stop_loss, source=excluded.source, "
        "skipped_debate=excluded.skipped_debate, data_json=excluded.data_json",
        (rnd, data.get("action"), _txt(data.get("stop_loss")), js, now, did),
    )


def decision_id(conn: sqlite3.Connection, d: date, market: str, ticker: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM decisions WHERE date=? AND market=? AND ticker=?",
        (d.isoformat(), market, ticker),
    ).fetchone()
    return int(row["id"]) if row else None


def _hydrate(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["data"] = json.loads(out["data_json"])
    return out


def get_decision(
    conn: sqlite3.Connection, decision_id: int, *, hydrate: bool = False
) -> dict | None:
    """결정 행. hydrate 면 `data_json` 을 파싱한 `data` 키를 더한다."""
    row = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    if row is None:
        return None
    return _hydrate(row) if hydrate else dict(row)


def latest_decision(
    conn: sqlite3.Connection, market: str, ticker: str, *, before: date | None = None
) -> dict | None:
    """종목의 가장 최근 결정 행(`data` 복원). before 가 있으면 그 날짜 **미만**만."""
    sql = "SELECT * FROM decisions WHERE market=? AND ticker=? "
    args: list[Any] = [market, ticker]
    if before is not None:
        sql += "AND date < ? "
        args.append(before.isoformat())
    row = conn.execute(sql + "ORDER BY date DESC, id DESC LIMIT 1", args).fetchone()
    return _hydrate(row) if row is not None else None


def set_skipped_debate(conn: sqlite3.Connection, decision_id: int, flag: bool) -> None:
    """결정 행에 토론 생략 여부(합의 시 생략)를 남긴다."""
    conn.execute("UPDATE decisions SET skipped_debate=? WHERE id=?", (int(bool(flag)), decision_id))
    conn.execute(
        "UPDATE decision_rounds SET skipped_debate=? WHERE decision_id=? AND round=0",
        (int(bool(flag)), decision_id),
    )
    conn.commit()


def decision_side(conn: sqlite3.Connection, decision_id: int) -> str | None:
    """BUY/ADD → "BUY", SELL/TRIM → "SELL", 그 외(HOLD·없는 결정) → None."""
    row = conn.execute("SELECT action FROM decisions WHERE id=?", (decision_id,)).fetchone()
    if row is None:
        return None
    if row["action"] in _BUY_ACTIONS:
        return "BUY"
    if row["action"] in _SELL_ACTIONS:
        return "SELL"
    return None


def upsert_ticker_name(
    conn: sqlite3.Connection, market: str, ticker: str, name: str | None, source: str
) -> None:
    """관측한 종목명을 적는다(최근 관측 우선). 빈 이름·코드와 같은 이름은 무시한다."""
    clean = (name or "").strip()
    if not clean or clean == ticker:
        return
    conn.execute(
        "INSERT INTO ticker_names(market, ticker, name, source, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(market, ticker) DO UPDATE SET "
        "name=excluded.name, source=excluded.source, updated_at=excluded.updated_at "
        "WHERE name IS NOT excluded.name OR source IS NOT excluded.source",  # 같은 값이면 쓰기 없음
        (market, ticker, clean, source, now_iso()),
    )
    conn.commit()


def upsert_ticker_exchange(
    conn: sqlite3.Connection, market: str, ticker: str, exchange: str | None
) -> None:
    """관측한 US 거래소 코드를 남긴다. KR·빈 값은 무시, 같은 값이면 쓰기 없음."""
    clean = (exchange or "").strip()
    if market != "US" or not clean:
        return
    conn.execute(
        "INSERT INTO ticker_exchanges(market, ticker, exchange, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(market, ticker) DO UPDATE SET exchange=excluded.exchange, "
        "updated_at=excluded.updated_at WHERE exchange IS NOT excluded.exchange",
        (market, ticker, clean, now_iso()),
    )
    conn.commit()


def exchange_of(conn: sqlite3.Connection, market: str, ticker: str) -> str | None:
    row = conn.execute(
        "SELECT exchange FROM ticker_exchanges WHERE market=? AND ticker=?", (market, ticker)
    ).fetchone()
    return str(row["exchange"]) if row else None
