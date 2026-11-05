"""잔고 주기 갱신: 브로커 잔고를 다시 읽어 `positions_snapshot` 을 갱신한다 — 조회만, 주문 없음.

데몬 `balance_refresh` interval 잡이 정규장 동안 매 틱(장 밖은 `OFF_HOURS_EVERY` 마다 — 입출금·
이체는 장 밖에도 일어난다), 체결 동기화 큐 요청이 끝난 직후(`only_stale=False`) 부른다. 스냅샷
기록은 판정과 같은 `executor.build_portfolio` 를 그대로 쓴다(섹터 자동 분류는 옛 동작에서 뺐다).

`regime`(RegimePort) 이 있으면 실제 NAV 로 누적 손실 킬스위치를 관측하고(`risk.
killswitch_drawdown_pct`), `cancel_buys`·`cancel_fn` 이 있으면(데몬 잔고 갱신, dry-run 아님·장중)
킬스위치가 켜진 계좌의 당일 미체결 BUY 를 거둔다 — `excluded_tickers`(헤지·현금 운용 ETF)는
남긴다(옛 `on_killswitch`). `notifier` 가 있으면 관측이 새로 켠 킬스위치를 한 번 알린다."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from trader.features.spot import executor
from trader.features.spot.excluded import excluded_tickers
from trader.kernel.broker.errors import KisApiError
from trader.kernel.config import market_hours
from trader.kernel.session.run_store import run_date_for

logger = logging.getLogger("trader.spot")

STALE_AFTER = timedelta(minutes=2)  # 페이지 접속 때 이보다 오래된 스냅샷만 다시 읽는다
OFF_HOURS_STALE = timedelta(minutes=10)  # 장 밖 페이지 접속 기준(이체·입출금 반영)
OFF_HOURS_EVERY = timedelta(minutes=30)  # 장 밖 데몬 주기 갱신 기준
# 시장별 실행 중 표시: 잡과 큐 요청(같은 프로세스)이 같은 시장 잔고를 겹쳐 읽지 않게
_RUNNING: dict[str, threading.Lock] = {"KR": threading.Lock(), "US": threading.Lock()}


def snapshot_age(conn: sqlite3.Connection, account_id: str, market: str, now: datetime) -> float:
    """최신 스냅샷 기록 뒤 지난 초. 없으면 무한대. 보유 0건이어도 NAV 행(`account_nav`)이 남아
    신선도가 보인다."""
    row = conn.execute(
        "SELECT MAX(created_at) FROM account_nav WHERE account_id=? AND market=?",
        (account_id, market),
    ).fetchone()
    if not row or not row[0]:
        return float("inf")
    try:
        return (now - datetime.fromisoformat(row[0])).total_seconds()
    except ValueError:
        return float("inf")


def open_markets(markets: list[str], now: datetime) -> list[str]:
    return [m for m in markets if market_hours.is_open(m, now)]


def refresh(
    conn: sqlite3.Connection,
    *,
    accounts: list,
    brokers: dict[str, Any],
    market: str,
    settings: Any,
    now: datetime,
    state: Path | None = None,
    only_stale: bool = False,
    stale_after: timedelta = STALE_AFTER,
    regime: Any | None = None,
    cancel_buys: bool = False,
    cancel_fn: Callable[..., tuple[int, list[str]]] | None = None,
    notifier: Any | None = None,
    scan: Any | None = None,
) -> dict:
    """시장 하나의 계좌별 잔고를 다시 읽는다. {refreshed: [id], skipped: [id], errors: {id: 사유}}.
    계좌 하나의 실패는 그 계좌만 errors 로. 같은 시장 갱신이 이미 돌고 있으면 기다리지 않고
    {busy: True}. 현물(stock) 계좌만 읽는다. `scan`(scan 포트)이 있으면 읽은 보유 종목을
    `classify` 로 넘겨 yaml 에 없는 종목의 섹터를 자동 분류한다(실패는 로그만)."""
    lock = _RUNNING.setdefault(market, threading.Lock())
    if not lock.acquire(blocking=False):
        return {"busy": True, "refreshed": [], "skipped": [], "errors": {}}
    try:
        out: dict = {"refreshed": [], "skipped": [], "errors": {}}
        keep = excluded_tickers(settings, regime, market)
        held: dict[str, str | None] = {}  # 보유 종목 → 거래소(US 업종 조회용)
        for a in accounts:
            if market not in a.markets or a.id not in brokers:
                continue
            if getattr(a, "asset", "stock") != "stock":
                continue
            if only_stale and snapshot_age(conn, a.id, market, now) < stale_after.total_seconds():
                out["skipped"].append(a.id)
                continue
            try:
                pf = executor.build_portfolio(
                    conn,
                    brokers[a.id],
                    a,
                    market,
                    run_date_for(market, now),
                    now=now,
                    record=True,
                    hedge_tickers=keep,
                    killswitch_pct=settings.risk.killswitch_drawdown_pct,
                    regime=regime,
                    cancel_buys=cancel_buys,
                    cancel_fn=cancel_fn,
                    notifier=notifier,
                )
                out["refreshed"].append(a.id)
                held.update({t: p.exchange for t, p in pf.positions.items()})
            except (KisApiError, ValueError) as e:
                out["errors"][a.id] = f"{type(e).__name__}: {e}"
                logger.warning("잔고 갱신 실패 %s %s: %s", a.id, market, e)
        if scan is not None and held:
            try:
                exchanges = {t: ex for t, ex in held.items() if ex}
                scan.classify(conn, market, sorted(held), exchanges=exchanges or None)
            except Exception as e:  # noqa: BLE001 - 섹터 자동 분류는 표시 보조, 잔고 갱신은 지킨다
                logger.warning("섹터 자동 분류 실패 %s: %s", market, e)
        return out
    finally:
        lock.release()
