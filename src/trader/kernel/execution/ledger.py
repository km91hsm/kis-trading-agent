"""execution 원장 원시 함수: 주문·승인·리스크 판정·스냅샷·NAV 기록과 게이트 입력용 조회.

옛 `ledger/repo.py` 의 함수를 같은 시그니처·같은 공식으로 옮겼다(게이트 입력이라 공식이 바뀌면
매매가 바뀐다). 금액은 Decimal ↔ TEXT, 시각은 UTC ISO 문자열(하루 경계는 `day_bounds`).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from trader.kernel.broker.models import Position
from trader.kernel.config.time import KST, MARKET_TZ
from trader.kernel.execution.types import ORIGIN_CHECK, dead_status_sql
from trader.kernel.ledger.db import now_iso

# LLM 결정의 주 2회 한도·5일 실현손익 성향은 llm·stop 만 센다 — 단타·리밸런스·재호가·헤지가 LLM
# 결정의 한도를 먹지 않게(2-O8)
LLM_ORIGIN_SQL = "COALESCE(origin, 'llm') IN ('llm', 'stop')"

# 승인 order_json 에서 열로 옮긴 사이드채널 키 → 열 이름
_SIDE_KEYS = {"quote_price": "quote_price", "order_stage": "stage", "exec_key": "exec_key",
              "origin": "origin"}  # fmt: skip


def _dec(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def day_bounds(d: date, market: str | None) -> tuple[str, str]:
    """시장 현지 날짜 d 하루의 UTC ISO 경계 [start, end). 시장이 없거나 모르면 KST.

    US 는 KST 자정을 넘겨 장이 열리므로 KST 창으로 보면 같은 세션의 주문이 두 날에 갈린다."""
    tz = MARKET_TZ.get(market or "", KST)  # type: ignore[call-overload]
    start = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(UTC)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


# --- 판정·주문 ----------------------------------------------------------------


def insert_risk_check(
    conn: sqlite3.Connection,
    *,
    decision_id: int,
    account_id: str,
    approved: bool,
    adjusted_qty: int | None,
    reasons: list[str],
) -> int:
    cur = conn.execute(
        "INSERT INTO risk_checks(decision_id, account_id, approved, adjusted_qty, reasons_json, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            decision_id,
            account_id,
            int(approved),
            adjusted_qty,
            json.dumps(reasons, ensure_ascii=False),
            now_iso(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def insert_order(
    conn: sqlite3.Connection,
    *,
    decision_id: int | None,
    account_id: str,
    ticker: str,
    market: str,
    side: str,
    qty: int,
    order_type: str,
    price: Decimal | None,
    status: str,
    order_no: str | None,
    message: str,
    mode: str,
    created_at: str | None = None,
    amount: Decimal | None = None,
    org_no: str | None = None,
    asset: str = "stock",
    commit: bool = True,
    origin: str | None = None,
) -> int:
    """amount 는 가능금액 차감용 주문금액(시장가는 판정 때 현재가 × 수량). 없으면 qty × price.
    `commit=False` 는 호출자가 연 트랜잭션 안에서 쓴다(중간 commit 이 원자성을 깨지 않게).
    origin 은 실행 종류(`ORIGIN_CHECK`) — None 이면 llm."""
    origin = origin or "llm"
    if origin not in ORIGIN_CHECK:
        raise ValueError(f"알 수 없는 주문 origin: {origin}")
    if amount is None and price is not None:
        amount = Decimal(qty) * price
    cur = conn.execute(
        "INSERT INTO orders(decision_id, account_id, ticker, market, side, qty, order_type, price, "
        "status, order_no, message, mode, created_at, amount, org_no, asset, origin) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            decision_id,
            account_id,
            ticker,
            market,
            side,
            qty,
            order_type,
            _s(price),
            status,
            order_no,
            message,
            mode,
            created_at or now_iso(),
            _s(amount),
            org_no,
            asset,
            origin,
        ),
    )
    if commit:
        conn.commit()
    return int(cur.lastrowid)


def set_order_status(conn: sqlite3.Connection, order_id: int, status: str) -> None:
    conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    conn.commit()


def update_order_fill(
    conn: sqlite3.Connection,
    order_id: int,
    *,
    filled_qty: int,
    status: Literal["partial", "filled"],
) -> None:
    """누적 체결 수량과 부분·전량 체결 상태를 주문 행에 적는다."""
    conn.execute(
        "UPDATE orders SET filled_qty=?, status=? WHERE id=?", (filled_qty, status, order_id)
    )
    conn.commit()


def insert_fill(
    conn: sqlite3.Connection,
    *,
    order_id: int,
    account_id: str,
    market: str,
    ticker: str,
    side: str,
    qty: int,
    price: Decimal,
    filled_at: str,
    order_no: str | None,
    commit: bool = True,
) -> int | None:
    """체결 1건 적재. 같은 (주문, 시각, 수량, 가격) 은 무시하고 None (재동기화 멱등).
    `commit=False` 는 호출자가 연 트랜잭션 안에서 쓴다(`insert_order` 와 같다)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO fills(order_id, account_id, qty, price, filled_at, order_no, "
        "ticker, side, market) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (order_id, account_id, qty, str(price), filled_at, order_no, ticker, side, market),
    )
    if commit:
        conn.commit()
    return int(cur.lastrowid) if cur.rowcount == 1 else None


def open_limit_orders_today(
    conn: sqlite3.Connection,
    account_id: str,
    market: str,
    today: date,
    *,
    exclude_origins: tuple[str, ...] = ("stop", "hedge"),
) -> list[dict]:
    """운영일 today(시장 현지 하루)에 낸 살아 있는 주문 중 exclude_origins 가 아닌 것(id 순)."""
    lo, hi = day_bounds(today, market)
    sql = (
        "SELECT * FROM orders WHERE account_id=? AND market=? "
        f"AND status NOT IN ({dead_status_sql()}) AND created_at >= ? AND created_at < ?"
    )
    args: list = [account_id, market, lo, hi]
    if exclude_origins:
        sql += f" AND origin NOT IN ({','.join('?' * len(exclude_origins))})"
        args += list(exclude_origins)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]


def has_open_order_or_approval(
    conn: sqlite3.Connection,
    decision_id: int,
    account_id: str,
    side: str | None = None,
    *,
    include_filled: bool = True,
    now: str | None = None,
) -> bool:
    """같은 결정·계좌(·방향)에 살아 있는 주문(실패·거부·취소 제외)이나 pending 승인이 있으면 True.

    재평가는 같은 decisions 행을 갱신하므로 방향이 다르면(BUY 뒤 SELL) 허용한다.
    include_filled=False(재평가 라운드)면 이미 **체결 완료**된 주문은 보지 않는다. 첫 실행의
    재시도(같은 결정 재제출)는 체결분까지 막아 이중 매수를 피한다."""
    dead = dead_status_sql() + ("" if include_filled else ",'filled'")
    sql = f"SELECT 1 FROM orders WHERE decision_id=? AND account_id=? AND status NOT IN ({dead})"
    args: list = [decision_id, account_id]
    if side:
        sql += " AND side=?"
        args.append(side)
    if conn.execute(sql + " LIMIT 1", args).fetchone():
        return True
    sql = "SELECT 1 FROM approvals WHERE decision_id=? AND account_id=? AND status='pending'"
    args = [decision_id, account_id]
    if now is not None:  # 만료됐는데 아직 expired 로 안 바뀐 요청은 살아 있지 않다
        sql += " AND expires_at > ?"
        args.append(now)
    if side:
        sql += " AND json_extract(order_json, '$.side')=?"
        args.append(side)
    return conn.execute(sql + " LIMIT 1", args).fetchone() is not None


def open_orders_today(
    conn: sqlite3.Connection,
    account_id: str,
    ticker: str | None,
    side: str,
    d: date,
    *,
    market: str | None = None,
) -> list[sqlite3.Row]:
    """오늘(시장 현지 하루) 같은 계좌·종목(None 이면 전 종목)·방향의 살아 있고 체결이 끝나지
    않은 주문 행(손절 전·결정 반전·킬스위치 취소 대상)."""
    lo, hi = day_bounds(d, market)
    where, args = "", [account_id, side, lo, hi]
    if ticker is not None:
        where, args = " AND ticker=?", [account_id, side, lo, hi, ticker]
    if market is not None:
        where += " AND market=?"
        args.append(market)
    return conn.execute(
        "SELECT id, ticker, order_no, org_no, qty, market, status, order_type, price FROM orders "
        "WHERE account_id=? AND side=? "
        f"AND status NOT IN ({dead_status_sql()},'filled') "
        "AND created_at >= ? AND created_at < ?" + where + " ORDER BY id",
        args,
    ).fetchall()


def today_buy_tickers(
    conn: sqlite3.Connection, account_id: str, d: date, market: str | None = None
) -> set[str]:
    """운영일 d(시장 현지 하루)에 낸 살아 있는 BUY 종목. 통화가 달라 market 으로도 거른다."""
    lo, hi = day_bounds(d, market)
    sql = (
        "SELECT DISTINCT ticker FROM orders WHERE account_id=? AND side IN ('BUY') "
        f"AND status NOT IN ({dead_status_sql()}) "
        "AND created_at >= ? AND created_at < ?"
    )
    args: list = [account_id, lo, hi]
    if market:
        sql += " AND market=?"
        args.append(market)
    return {r["ticker"] for r in conn.execute(sql, args).fetchall()}


def ledger_marks(conn: sqlite3.Connection) -> dict:
    """포트폴리오가 원장을 읽는 순간의 표시 {order_id, approval_id, at}(그때의 최대 id·시각) —
    진행 중 예약이 '포트폴리오가 이미 센 주문·승인' 을 시각 추정이 아니라 이것으로 가린다."""
    o = conn.execute("SELECT COALESCE(MAX(id), 0) FROM orders").fetchone()[0]
    a = conn.execute("SELECT COALESCE(MAX(id), 0) FROM approvals").fetchone()[0]
    return {"order_id": int(o), "approval_id": int(a), "at": now_iso()}


# --- 게이트 입력 --------------------------------------------------------------


def _buy_amounts_today(
    conn: sqlite3.Connection,
    account_id: str,
    d: date,
    market: str | None,
    *,
    exclude_filled: bool,
    now: str | None,
) -> list[tuple[str, Decimal]]:
    """운영일 d 의 살아 있는 BUY 주문 + 만료 전 pending BUY 승인의 (종목, 금액) 목록."""
    lo, hi = day_bounds(d, market)
    excluded = dead_status_sql() + (",'filled'" if exclude_filled else "")
    sql = (
        "SELECT ticker, qty, price, amount FROM orders WHERE account_id=? AND side='BUY' "
        f"AND status NOT IN ({excluded}) AND created_at >= ? AND created_at < ?"
    )
    args: list = [account_id, lo, hi]
    if market:
        sql += " AND market=?"
        args.append(market)
    out: list[tuple[str, Decimal]] = []
    for r in conn.execute(sql, args).fetchall():
        if r["amount"] is not None:
            out.append((r["ticker"], _dec(r["amount"])))
        else:
            out.append((r["ticker"], Decimal(r["qty"]) * _dec(r["price"])))
    # pending 승인 요청도 현금을 예약한다 — 승인이 누적되면 가능금액을 넘겨 과매수하기 때문
    sql = (
        "SELECT order_json, quote_price FROM approvals WHERE account_id=? AND status='pending' "
        "AND json_extract(order_json, '$.side')='BUY' AND requested_at >= ? AND requested_at < ?"
    )
    args = [account_id, lo, hi]
    if now is not None:  # 만료 요청은 현금을 더 예약하지 않는다 — expire 잡이 안 돌아도
        sql += " AND expires_at > ?"
        args.append(now)
    if market:
        sql += " AND json_extract(order_json, '$.market')=?"
        args.append(market)
    for r in conn.execute(sql, args).fetchall():
        o = json.loads(r["order_json"])
        price = o.get("price") if o.get("price") is not None else r["quote_price"]
        if price is not None:
            out.append((str(o.get("ticker")), Decimal(int(o.get("qty") or 0)) * _dec(price)))
    return out


def pending_buy_amounts(
    conn: sqlite3.Connection,
    account_id: str,
    d: date,
    market: str | None = None,
    *,
    now: str | None = None,
) -> dict[str, Decimal]:
    """체결 전 BUY 주문(accepted·partial·unknown) + pending 승인의 종목별 금액(예상 포트폴리오).
    체결 완료 주문은 브로커 잔고에 이미 보유로 잡히므로 뺀다(partial 은 주문 전체 금액 — 체결분이
    보유와 겹쳐 보수적으로 센다)."""
    out: dict[str, Decimal] = {}
    for t, v in _buy_amounts_today(conn, account_id, d, market, exclude_filled=True, now=now):
        out[t] = out.get(t, Decimal(0)) + v
    return out


def today_order_amount(
    conn: sqlite3.Connection,
    account_id: str,
    d: date,
    market: str | None = None,
    *,
    exclude_filled: bool = False,
    now: str | None = None,
) -> Decimal:
    """운영일 d(시장 현지 하루)의 누적 매수 주문금액. 통화가 달라 market 으로 거른다.
    exclude_filled=True 면 체결 완료 주문은 빼고 미체결(accepted·partial·unknown)만 더한다 —
    주문가능금액(D+2 정산 반영)이 체결분을 이미 뺐을 때 이중 차감을 막는다."""
    return sum(
        (
            v
            for _, v in _buy_amounts_today(
                conn, account_id, d, market, exclude_filled=exclude_filled, now=now
            )
        ),
        Decimal(0),
    )


def trades_this_week(
    conn: sqlite3.Connection, account_id: str, d: date, market: str | None = None
) -> dict[str, int]:
    """운영일 d 가 속한 주(월요일 ~ d, 시장 현지 하루 경계)의 종목별 살아 있는 주문 수.
    LLM 결정 주문(llm·stop)만 — 단타·리밸런스·재호가·헤지는 세지 않는다(2-O8)."""
    monday = d - timedelta(days=d.weekday())
    lo, _ = day_bounds(monday, market)
    _, hi = day_bounds(d, market)
    sql = (
        "SELECT ticker, COUNT(*) AS n FROM orders WHERE account_id=? "
        f"AND status NOT IN ({dead_status_sql()}) AND {LLM_ORIGIN_SQL} "
        "AND created_at >= ? AND created_at < ?"
    )
    args: list = [account_id, lo, hi]
    if market:
        sql += " AND market=?"
        args.append(market)
    rows = conn.execute(sql + " GROUP BY ticker", args).fetchall()
    return {r["ticker"]: int(r["n"]) for r in rows}


def realized_pnl_5d(
    conn: sqlite3.Connection, account_id: str, d: date, market: str | None = None
) -> Decimal:
    """최근 5거래일(달력 7일 근사) 실현손익: 매도 체결금액 − **현재 보유 묶음의 평균 매수가**.

    창 안에 SELL 체결이 있는 종목만 전체 체결을 시간순으로 걸어 평균가를 갱신한다(보유 0 이면 리셋).
    전 이력 BUY 평균을 쓰면 전량 매도 뒤 재매수한 종목의 손익 부호가 뒤집혀 성향 판정이 틀린다.
    금액은 Decimal 로만 계산한다. 통화가 섞이지 않게 market 으로 거른다. 손익에 넣는 매도는 LLM
    결정 주문(llm·stop)의 체결만 — 단타 왕복 손실이 다음 날 LLM 한도를 줄이지 않게(2-O8) — 이되
    보유 묶음의 수량·평단은 **모든 origin 의 체결**로 재구성한다.
    """
    lo, _ = day_bounds(d - timedelta(days=7), market)  # 시장 현지 창(US 는 KST 자정을 넘긴다)
    _, hi = day_bounds(d, market)
    origin_sql = LLM_ORIGIN_SQL.replace("origin", "o.origin", 1)
    sql = (
        "SELECT DISTINCT o.ticker FROM fills f JOIN orders o ON o.id=f.order_id "
        f"WHERE f.account_id=? AND o.side='SELL' AND {origin_sql} "
        "AND f.filled_at >= ? AND f.filled_at < ?"
    )
    args: list = [account_id, lo, hi]
    if market:
        sql += " AND o.market=?"
        args.append(market)
    tickers = [r["ticker"] for r in conn.execute(sql, args).fetchall()]
    if not tickers:
        return Decimal(0)
    marks = ",".join("?" * len(tickers))
    sql = (
        "SELECT o.ticker, o.side, f.qty, f.price, f.filled_at, "
        f"({origin_sql}) AS llm FROM fills f JOIN orders o ON o.id=f.order_id "
        f"WHERE f.account_id=? AND o.ticker IN ({marks})"
    )
    args = [account_id, *tickers]
    if market:
        sql += " AND o.market=?"
        args.append(market)
    rows = conn.execute(sql + " ORDER BY f.filled_at, f.id", args).fetchall()
    total = Decimal(0)
    qty: dict[str, int] = {}
    cost: dict[str, Decimal] = {}
    for r in rows:
        t = r["ticker"]
        q = int(r["qty"])
        px = _dec(r["price"])
        if r["side"] == "BUY":
            qty[t] = qty.get(t, 0) + q
            cost[t] = cost.get(t, Decimal(0)) + px * q
            continue
        held = qty.get(t, 0)
        if held <= 0:
            # 매수 체결 기록이 없는 보유분(fills sync 이전·HTS 매수): 평단을 몰라 손익에 넣지 않는다
            # (평단 0 으로 치면 매도금액 전체가 이익이 돼 성향이 틀린다)
            continue
        avg = cost.get(t, Decimal(0)) / held
        if r["llm"] and lo <= r["filled_at"] < hi:
            total += (px - avg) * q
        remaining = held - q
        if remaining <= 0:
            qty[t], cost[t] = 0, Decimal(0)
        else:
            qty[t], cost[t] = remaining, avg * remaining
    return total


def _first_buy_of_current_lot(
    events: list[tuple[str, str, int]], market: str | None = None
) -> date | None:
    """(시각, 방향, 수량) 시간순 이벤트에서 현재 보유 묶음의 첫 매수일. 보유가 0 이면 None.

    전량 매도 뒤 다시 사면 그 매수일부터 센다 (최소 보유기간이 재진입에도 적용되도록).
    날짜는 시장 현지(US 는 ET): KST 로 보면 ET 오전 체결이 다음 날이 돼 보유 거래일이 하루 준다."""
    tz = MARKET_TZ.get(market or "", KST)  # type: ignore[call-overload]
    qty = 0
    since: date | None = None
    for at, side, n in events:
        if side == "BUY":
            if qty <= 0:
                since = datetime.fromisoformat(at).astimezone(tz).date()
                qty = 0
            qty += int(n)
        else:
            qty -= int(n)
            if qty <= 0:
                qty, since = 0, None
    return since if qty > 0 else None


def held_since(
    conn: sqlite3.Connection, account_id: str, tickers: list[str] | None = None
) -> dict[str, date]:
    """종목별 현재 보유 묶음의 첫 매수일 (최소 보유기간용). 전량 매도 뒤 재매수면 그 날부터.

    체결 기록이 있는 종목은 체결로, 없는 종목은 접수된 주문(매수 +, 매도 −)으로 계산한다.
    보유가 0 인 종목은 돌려주지 않는다. tickers 를 주면 그 종목만 읽는다 (판정마다 계좌 전체
    이력을 읽지 않도록; 결과는 같다).
    """
    if tickers is not None and not tickers:
        return {}
    scope, scope_args = "", []
    if tickers:
        scope = f" AND o.ticker IN ({','.join('?' * len(tickers))})"
        scope_args = list(tickers)
    by_ticker: dict[str, list[tuple[str, str, int]]] = {}
    markets: dict[str, str] = {}
    for r in conn.execute(
        "SELECT o.ticker, o.market, o.side, f.qty, f.filled_at FROM fills f "
        f"JOIN orders o ON o.id=f.order_id WHERE f.account_id=?{scope} ORDER BY f.filled_at, f.id",
        (account_id, *scope_args),
    ).fetchall():
        by_ticker.setdefault(r["ticker"], []).append((r["filled_at"], r["side"], r["qty"]))
        markets[r["ticker"]] = r["market"]
    out: dict[str, date] = {}
    for t, events in by_ticker.items():
        since = _first_buy_of_current_lot(events, markets.get(t))
        if since is not None:
            out[t] = since
    fallback: dict[str, list[tuple[str, str, int]]] = {}
    for r in conn.execute(
        "SELECT ticker, market, side, qty, created_at FROM orders o WHERE account_id=? "
        f"AND status NOT IN ({dead_status_sql()}){scope} ORDER BY created_at, id",
        (account_id, *scope_args),
    ).fetchall():
        if r["ticker"] in by_ticker:
            continue  # 체결 기록이 있는 종목은 체결이 정본
        fallback.setdefault(r["ticker"], []).append((r["created_at"], r["side"], r["qty"]))
        markets[r["ticker"]] = r["market"]
    for t, events in fallback.items():
        since = _first_buy_of_current_lot(events, markets.get(t))
        if since is not None:
            out[t] = since
    return out


# --- 스냅샷·NAV ---------------------------------------------------------------


def snapshot_positions(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    d: date,
    nav: Decimal,
    cash: Decimal,
    positions: list[Position] | list[dict],
    market: str,
    asset: str = "stock",
) -> None:
    """계좌·날짜·시장·종목 단위 upsert(positions_snapshot) + 계좌 NAV upsert(account_nav).

    같은 날 앞선 스냅샷에 있던 종목이 이번에 없으면(전량 매도) 그 행을 지운다 — 웹·트레이더 입력이
    유령 보유를 보지 않도록. 같은 시장·자산의 행만 지운다. 포지션이 없어도 NAV 는 남긴다."""
    created = now_iso()
    day = d.isoformat()
    keep: list[str] = []
    for p in positions:
        row = p.model_dump() if isinstance(p, Position) else p
        keep.append(row["ticker"])
        conn.execute(
            "INSERT INTO positions_snapshot(account_id, date, market, asset, ticker, qty, "
            "avg_price, current_price, eval_amount, created_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(account_id, date, market, asset, ticker) DO UPDATE SET "
            "qty=excluded.qty, avg_price=excluded.avg_price, "
            "current_price=excluded.current_price, eval_amount=excluded.eval_amount, "
            # created_at = 마지막 관측 시각(같은 날 다시 읽으면 갱신 — 웹 실시간 지문·신선도)
            "created_at=excluded.created_at",
            (
                account_id, day, market, asset, row["ticker"], int(row.get("qty", 0)),
                _s(row.get("avg_price")), _s(row.get("current_price")),
                _s(row.get("eval_amount")), created,
            ),
        )  # fmt: skip
    marks = ",".join("?" * len(keep))
    conn.execute(
        "DELETE FROM positions_snapshot WHERE account_id=? AND date=? AND market=? AND asset=? "
        f"AND ticker NOT IN ({marks})",
        (account_id, day, market, asset, *keep),
    )
    conn.execute(
        "INSERT INTO account_nav(account_id, date, market, nav, cash, created_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(account_id, date, market) DO UPDATE SET "
        "nav=excluded.nav, cash=excluded.cash, created_at=excluded.created_at",
        (account_id, day, market, str(nav), str(cash), created),
    )
    conn.commit()


def nav_history(
    conn: sqlite3.Connection,
    account_id: str,
    market: str,
    *,
    days: int = 30,
    today: date | None = None,
    has_session: Callable[[str, date], bool] | None = None,
) -> list[tuple[str, Decimal]]:
    """(today 또는 마지막 NAV 날짜) − days 이후의 (날짜, NAV) 오름차순.

    스냅샷 개수가 아니라 날짜 창이다: 운영이 띄엄띄엄이어도 오래된 값이 섞이지 않는다.
    **거래일 표본만**: `has_session(market, 날짜)` 가 False 인 날은 뺀다(장 밖 잔고 갱신이 남긴
    주말·휴장일이 거래일 수익률·연율화 지표를 바꾸지 않게). 주지 않으면 주말만 뺀다."""
    has_session = has_session or (lambda _m, day: day.weekday() < 5)
    if today is None:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM account_nav WHERE account_id=? AND market=?",
            (account_id, market),
        ).fetchone()
        if not row or not row["d"]:
            return []
        end = date.fromisoformat(row["d"])
    else:
        end = today
    lo = (end - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT date, nav FROM account_nav WHERE account_id=? AND market=? AND date >= ? "
        "ORDER BY date",
        (account_id, market, lo),
    ).fetchall()
    return [
        (r["date"], _dec(r["nav"]))
        for r in rows
        if has_session(market, date.fromisoformat(r["date"]))
    ]


def latest_nav_before(
    conn: sqlite3.Connection, account_id: str, d: date, market: str | None = None
) -> Decimal | None:
    """d 이전 마지막 NAV (일일 손익 기준). 시장별 통화가 달라 market 으로 거른다."""
    sql = "SELECT nav FROM account_nav WHERE account_id=? AND date < ?"
    args: list = [account_id, d.isoformat()]
    if market:
        sql += " AND market=?"
        args.append(market)
    row = conn.execute(sql + " ORDER BY date DESC, id DESC LIMIT 1", args).fetchone()
    return _dec(row["nav"]) if row else None


# --- 승인 ---------------------------------------------------------------------


def create_approval(
    conn: sqlite3.Connection,
    *,
    decision_id: int,
    account_id: str,
    order: dict,
    requested_at: str,
    expires_at: str,
    fingerprint: str,
    stage: str | None = None,
    exec_key: str | None = None,
    origin: str | None = None,
    quote_price: Decimal | str | None = None,
) -> int:
    """승인 요청을 pending 으로 만든다. 사이드채널 값(quote_price·order_stage·exec_key·origin)은
    `order` 에 들어 있어도 order_json 이 아니라 열에 둔다(명시 인자가 우선)."""
    body = dict(order)
    given = {"quote_price": quote_price, "order_stage": stage, "exec_key": exec_key,
             "origin": origin}  # fmt: skip
    cols = {col: body.pop(key, None) for key, col in _SIDE_KEYS.items()}
    for key, col in _SIDE_KEYS.items():
        if given[key] is not None:
            cols[col] = given[key]
    cur = conn.execute(
        "INSERT INTO approvals(decision_id, account_id, order_json, status, requested_at, "
        "expires_at, fingerprint, stage, exec_key, origin, quote_price) "
        "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)",
        (
            decision_id,
            account_id,
            json.dumps(body, ensure_ascii=False),
            requested_at,
            expires_at,
            fingerprint,
            _s(cols["stage"]),
            _s(cols["exec_key"]),
            _s(cols["origin"]),
            _s(cols["quote_price"]),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def _approval_row(r: sqlite3.Row) -> dict:
    """열로 옮긴 사이드채널 값을 `order` dict 에 다시 합쳐 옛 호출부와 맞춘다."""
    d = dict(r)
    order = json.loads(d.pop("order_json"))
    for key, col in _SIDE_KEYS.items():
        if d[col] is not None:
            order[key] = d[col]
    d["order"] = order
    return d


def get_approval(conn: sqlite3.Connection, approval_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    return _approval_row(r) if r else None


def pending_approvals(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM approvals WHERE status='pending' ORDER BY id").fetchall()
    return [_approval_row(r) for r in rows]


def decide_approval(
    conn: sqlite3.Connection,
    approval_id: int,
    status: str,
    *,
    decided_at: str | None = None,
    expect: str = "pending",
    unexpired_at: str | None = None,
) -> bool:
    """`expect` 상태일 때만 바꾼다(조건부 UPDATE). 다른 프로세스가 먼저 처리했으면 False.

    `unexpired_at` 을 주면 `expires_at > unexpired_at` 인 행만 바꾼다 — 승인 실행 직전에 만료된
    요청이 approved 로 굳지 않게 한다(만료로 0행이면 역시 False)."""
    sql = "UPDATE approvals SET status=?, decided_at=? WHERE id=? AND status=?"
    params: tuple = (status, decided_at or now_iso(), approval_id, expect)
    if unexpired_at is not None:
        sql += " AND expires_at > ?"
        params += (unexpired_at,)
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.rowcount == 1


def expire_pending(conn: sqlite3.Connection, now: str) -> list[int]:
    """`expires_at <= now` 인 pending 승인을 expired 로 바꾸고 바꾼 id 를 돌려준다."""
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM approvals WHERE status='pending' AND expires_at <= ? ORDER BY id",
            (now,),
        ).fetchall()
    ]
    return [i for i in ids if decide_approval(conn, i, "expired", decided_at=now)]


def expire_pending_buys(
    conn: sqlite3.Connection, *, account_id: str, market: str, ticker: str, now: str
) -> list[int]:
    """그 계좌·시장·종목의 pending BUY 승인 요청을 만료로 닫는다(손절 직후 사람이 옛 요청을 승인해
    곧바로 되사는 것을 막는다). 닫은 승인 id."""
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM approvals WHERE account_id=? AND status='pending' "
            "AND json_extract(order_json, '$.ticker')=? "
            "AND COALESCE(json_extract(order_json, '$.market'), ?)=? "
            "AND json_extract(order_json, '$.side')='BUY' ORDER BY id",
            (account_id, ticker, market, market),
        ).fetchall()
    ]
    return [i for i in ids if decide_approval(conn, i, "expired", decided_at=now)]


def latest_positions(
    conn: sqlite3.Connection, account_id: str, market: str, *, asset: str = "stock"
) -> dict | None:
    """해당 계좌·시장·자산(기본 현물)의 최신 스냅샷 날짜 포지션 `{date, nav, cash, positions}`.
    없으면 None. 날짜는 positions_snapshot·account_nav 중 최신(전량 청산일은 빈 positions).
    nav·cash 는 같은 날짜의 `account_nav` 행(없으면 None)."""
    # 전량 청산한 날은 positions_snapshot 행이 없다 → 날짜는 두 표 중 최신(NAV 행이 항상 남는다)
    row = conn.execute(
        "SELECT MAX(d) AS date FROM ("
        "SELECT MAX(date) AS d FROM positions_snapshot WHERE account_id=? AND market=? "
        "AND asset = ? "
        "UNION ALL SELECT MAX(date) FROM account_nav WHERE account_id=? AND market=?)",
        (account_id, market, asset, account_id, market),
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
            "FROM positions_snapshot WHERE account_id=? AND market=? AND date=? AND asset = ? "
            "AND ticker NOT LIKE '\\_CASH%' ESCAPE '\\' ORDER BY ticker",
            (account_id, market, d, asset),
        )
    ]
    return {
        "date": d,
        "nav": nav["nav"] if nav else None,
        "cash": nav["cash"] if nav else None,
        "positions": positions,
    }


# --- 결정·트리거·거래소 조회 (spot 에서 승격) ------------------------------------------


def _js(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


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


def exchange_of(conn: sqlite3.Connection, market: str, ticker: str) -> str | None:
    row = conn.execute(
        "SELECT exchange FROM ticker_exchanges WHERE market=? AND ticker=?", (market, ticker)
    ).fetchone()
    return str(row["exchange"]) if row else None


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
