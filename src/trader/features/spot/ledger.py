"""spot 결정 저장소: 체크포인트(10_/20_/30_/31_) → 원장 테이블 멱등 적재와 조회.

원장 연결은 호출자가 소유한다. 쓰기는 호출마다 commit 한다(행 단위 멱등)."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any

from trader.kernel.execution.ledger import day_bounds
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
    m_round = None
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
    elif _DECISION_RE.match(stage) or (m_round := _ROUND_RE.match(stage)) is not None:
        # 30_decision 과 재평가 31_decision_r<n> 모두 (date, market, ticker) 결정 행을 덮어쓴다
        # (옛 동작: 최신 결정이 행이 된다). 손절 바닥은 아래 라운드 행(stop_loss)에서 유지한다
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
        if did is not None and m_round is None:
            _upsert_round(conn, did, 0, data, js, now)
        elif did is not None:
            rdata, rjs = _with_stop_floor(conn, did, market, ticker, data, js)
            _upsert_round(conn, did, int(m_round.group(1)), rdata, rjs, now)
    conn.commit()


def _with_stop_floor(
    conn: sqlite3.Connection, did: int, market: str, ticker: str, data: dict, js: str
) -> tuple[dict, str]:
    """재평가 라운드(BUY/ADD/HOLD)의 손절가를 같은 날 앞선 라운드들의 바닥(`max_stop_since`)
    이상으로 올린다 — 재평가 HOLD 가 손절가를 비우거나 낮춰도 아침 손절선이 산다(Ruling 13).
    SELL/TRIM 은 바닥 대상이 아니라 손대지 않는다. 바닥이 없거나 이미 높으면 그대로."""
    if data.get("action") not in ("BUY", "ADD", "HOLD"):
        return data, js
    floor = max_stop_since(conn, market, ticker, did)
    if floor is None:
        return data, js
    cur = data.get("stop_loss")
    if cur is not None and Decimal(str(cur)) >= floor:
        return data, js
    raised = {**data, "stop_loss": float(floor)}
    return raised, _js(raised)


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


# --- 트리거 (monitor) -----------------------------------------------------------------


def insert_trigger(
    conn: sqlite3.Connection,
    *,
    market: str,
    ticker: str,
    kind: str,
    detail: dict,
    price: Decimal | None,
    decision_id: int | None,
    account_id: str | None,
    created_at: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO triggers(account_id, market, ticker, kind, detail, price, decision_id, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            account_id,
            market,
            ticker,
            kind,
            _js(detail),
            None if price is None else str(price),
            decision_id,
            created_at,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def mark_trigger_handled(
    conn: sqlite3.Connection, trigger_id: int, marker: str, *, handled_at: str
) -> None:
    """트리거를 처리 완료로 표시한다. marker 는 세션 id 또는 '실패:…'(기동 실패)."""
    conn.execute(
        "UPDATE triggers SET handled_at=?, session_id=? WHERE id=?",
        (handled_at, marker, trigger_id),
    )
    conn.commit()


def get_trigger(conn: sqlite3.Connection, trigger_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, market, ticker, kind, detail, price, decision_id, handled_at FROM triggers "
        "WHERE id=?",
        (trigger_id,),
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    try:
        out["detail"] = json.loads(out["detail"]) if out.get("detail") else {}
    except ValueError:
        out["detail"] = {}
    return out


def unhandled_trigger_id(
    conn: sqlite3.Connection, market: str, ticker: str, kind: str, today: date
) -> int | None:
    """운영일 today(시장 현지 하루)에 쌓인 같은 종목·종류의 미처리 트리거 id. 어제 처리 못 한
    트리거가 이월돼 오늘 재평가가 옛 가격·사유로 돌지 않게 한다."""
    lo, hi = day_bounds(today, market)
    row = conn.execute(
        "SELECT id FROM triggers WHERE market=? AND ticker=? AND kind=? AND handled_at IS NULL "
        "AND created_at >= ? AND created_at < ? ORDER BY id LIMIT 1",
        (market, ticker, kind, lo, hi),
    ).fetchone()
    return int(row["id"]) if row else None


def reevaluated_between(
    conn: sqlite3.Connection, market: str, ticker: str, kind: str, lo: str, hi: str
) -> bool:
    """[lo, hi) 안에 같은 종류의 트리거가 재평가 세션까지 마쳤는가 (운영일 1회 제한용).

    기동 실패(marker 가 '실패:' 로 시작)는 세지 않아 30분 뒤 재시도할 수 있다."""
    row = conn.execute(
        "SELECT 1 FROM triggers WHERE market=? AND ticker=? AND kind=? "
        "AND handled_at >= ? AND handled_at < ? "
        "AND (session_id IS NULL OR session_id NOT LIKE '실패:%') LIMIT 1",
        (market, ticker, kind, lo, hi),
    ).fetchone()
    return row is not None


def last_trigger_handled_at(conn: sqlite3.Connection, market: str, ticker: str) -> str | None:
    """해당 종목의 마지막 재평가 시각 (30분 1회 제한용)."""
    row = conn.execute(
        "SELECT MAX(handled_at) AS t FROM triggers WHERE market=? AND ticker=? "
        "AND handled_at IS NOT NULL",
        (market, ticker),
    ).fetchone()
    return row["t"] if row and row["t"] else None


def latest_trigger_price(
    conn: sqlite3.Connection, market: str, ticker: str, today: date
) -> Decimal | None:
    """운영일 today 의 가장 최근 트리거 가격 (재평가의 '현재가'). 트리거가 없거나 가장 최근
    트리거에 가격이 없으면(뉴스 재평가) None."""
    lo, hi = day_bounds(today, market)
    row = conn.execute(
        "SELECT price FROM triggers WHERE market=? AND ticker=? "
        "AND created_at >= ? AND created_at < ? ORDER BY id DESC LIMIT 1",
        (market, ticker, lo, hi),
    ).fetchone()
    return Decimal(str(row["price"])) if row and row["price"] is not None else None


def max_stop_since(
    conn: sqlite3.Connection, market: str, ticker: str, since_decision_id: int | None
) -> Decimal | None:
    """since_decision_id 이후 그 종목 결정들의 BUY/ADD/HOLD 라운드 손절가 최고값(헤지 제외).

    손절선은 올리기만 한다 — 같은 날 재평가가 결정 행을 덮어써도, 보유 재분석의 HOLD 가 손절가를
    비우거나 낮춰도 이전 손절선을 지킨다. SELL/TRIM 의 손절가는 바닥에 넣지 않는다.
    기준 결정이 없으면 None."""
    if since_decision_id is None:
        return None
    rows = conn.execute(
        "SELECT r.stop_loss FROM decision_rounds r JOIN decisions d ON d.id = r.decision_id "
        "WHERE d.market=? AND d.ticker=? AND r.decision_id >= ? AND r.stop_loss IS NOT NULL "
        "AND r.action IN ('BUY','ADD','HOLD') AND COALESCE(r.source, '') != 'hedge'",
        (market, ticker, since_decision_id),
    ).fetchall()
    return max((Decimal(r["stop_loss"]) for r in rows), default=None)


def latest_positions(conn: sqlite3.Connection, account_id: str, market: str) -> dict | None:
    """해당 계좌·시장의 최신 스냅샷 날짜 포지션 `{date, nav, cash, positions}`. 없으면 None.
    날짜는 positions_snapshot·account_nav 중 최신(전량 청산일은 빈 positions).
    nav·cash 는 같은 날짜의 `account_nav` 행(없으면 None)."""
    # 전량 청산한 날은 positions_snapshot 행이 없다 → 날짜는 두 표 중 최신(NAV 행이 항상 남는다)
    row = conn.execute(
        "SELECT MAX(d) AS date FROM ("
        "SELECT MAX(date) AS d FROM positions_snapshot WHERE account_id=? AND market=? "
        "UNION ALL SELECT MAX(date) FROM account_nav WHERE account_id=? AND market=?)",
        (account_id, market, account_id, market),
    ).fetchone()
    if row is None or row["date"] is None:
        return None
    d = row["date"]
    nav = conn.execute(
        "SELECT nav, cash FROM account_nav WHERE account_id=? AND market=? AND date=?",
        (account_id, market, d),
    ).fetchone()
    positions = [
        dict(r)
        for r in conn.execute(
            "SELECT ticker, market, qty, avg_price, current_price, eval_amount "
            "FROM positions_snapshot WHERE account_id=? AND market=? AND date=? "
            "AND ticker NOT LIKE '\\_CASH%' ESCAPE '\\' ORDER BY ticker",
            (account_id, market, d),
        )
    ]
    return {
        "date": d,
        "nav": nav["nav"] if nav else None,
        "cash": nav["cash"] if nav else None,
        "positions": positions,
    }
