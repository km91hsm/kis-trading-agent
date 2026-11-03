"""열린 창 종목(전체 검토 2차 2-L1): 보유 + 미만기 메모리 + 미만기 섀도 종목을 일일 피처 스냅샷
대상에 더한다 — 유니버스 밖 LLM 매수 종목도 만기 봉이 생기게."""

import json
from datetime import date
from decimal import Decimal

from trader.features.evaluation import open_window, shadow
from trader.features.evaluation.memory import store
from trader.features.ingest.universe import Universe, UsSymbol
from trader.features.spot.ledger import upsert_ticker_exchange
from trader.kernel.execution import ledger as kledger

D = date(2026, 10, 9)


def _held(conn, account, market, *tickers, qty=1):
    """계좌·시장의 그날 잔고 스냅샷(같은 날 다시 찍으면 빠진 종목은 전량 매도 → 한 번에 찍는다)."""
    kledger.snapshot_positions(
        conn,
        account_id=account,
        d=D,
        market=market,
        nav=Decimal(100),
        cash=Decimal(10),
        positions=[
            {"ticker": t, "market": market, "qty": qty, "avg_price": "1", "current_price": "1"}
            for t in tickers
        ],
    )


def _memory(conn, market, ticker, decision_id, *, matured=False):
    mid = store.record_decision(
        conn,
        decision_id=decision_id,
        ticker=ticker,
        market=market,
        account_id="paper-main",
        decided_at="2026-10-09T00:00:00+00:00",
        decision={"action": "BUY", "confidence": 0.6},
        row=None,
        horizon_days=5,
        vector=[0.0] * 12,
    )
    if matured:
        conn.execute(
            "UPDATE memory_entries SET outcome_at='2026-10-20T00:00:00+00:00' WHERE id=?", (mid,)
        )
        conn.commit()


def test_tickers_union_of_held_pending_memory_and_pending_shadow(conn):
    _held(conn, "paper-main", "KR", "247540")
    _held(conn, "real-main", "KR", "000660", qty=0)  # 수량 0 은 보유가 아니다
    _held(conn, "paper-main", "US", "RIVN")
    _memory(conn, "KR", "096770", 1)
    _memory(conn, "KR", "035420", 2, matured=True)  # 만기 완료는 제외
    conn.execute(
        "INSERT INTO shadow_portfolios(market, date, strategy, weights_json, created_at, "
        "def_version) VALUES ('KR', '2026-10-09', 'llm', ?, 'x', 2), "
        "('KR', '2026-10-09', 'index_hold', ?, 'x', 2), "
        "('KR', '2026-10-01', 'candidates_1n', ?, 'x', 2)",
        (json.dumps({"402340": 1.0}), json.dumps({"069500": 1.0}), json.dumps({"005930": 1.0})),
    )
    conn.execute("UPDATE shadow_portfolios SET fwd_ret=0.0, matured_at='x' WHERE date='2026-10-01'")
    conn.commit()
    assert open_window.tickers(conn, "KR") == {"247540", "096770", "402340"}
    assert open_window.tickers(conn, "US") == {"RIVN"}
    assert shadow.INDEX_STRATEGIES == ("index_hold", "index_trend")  # 지수 proxy 는 피처 대상 아님


def test_targets_skip_universe_and_resolve_us_exchange(conn):
    universe = Universe(kr=["005930"], us=[UsSymbol(symbol="AAPL", exchange="NAS")])
    _held(conn, "paper-main", "KR", "005930", "247540")  # 005930 은 유니버스 안 → 이미 찍는다
    _held(conn, "paper-main", "US", "AAPL", "RIVN", "ZZZZ")  # ZZZZ 는 거래소 관측 없음 → 뺀다
    upsert_ticker_exchange(conn, "US", "RIVN", "NAS")
    upsert_ticker_exchange(conn, "KR", "247540", None)
    conn.commit()
    ex_us = open_window._exchanges(conn, "US")
    assert ex_us == {"RIVN": "NAS"}
    assert open_window._exchanges(conn, "KR") == {}
    assert open_window.targets(conn, "KR", universe, exchanges={}) == [("247540", "KR", None)]
    assert open_window.targets(conn, "US", universe, exchanges=ex_us) == [("RIVN", "US", "NAS")]
