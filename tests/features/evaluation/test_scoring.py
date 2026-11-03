"""전체 검토 L2·L3: 재평가 라운드별 엔트리 + 방향 점수·회피 손실·체결가 손익 (원장 v23)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from tests.features.ml.helpers import trading_days
from tests.features.spot.helpers import ok_decision
from trader.features.evaluation.memory import outcomes, store
from trader.features.evaluation.settings import MemorySettings
from trader.features.ml import api as ml_api
from trader.features.spot.ledger import ingest_checkpoint
from trader.kernel.execution import ledger as kledger

D0 = date(2026, 9, 1)
DAYS = trading_days(30, start=D0)
NOW = datetime(2026, 10, 17, tzinfo=UTC)


def _store(prices: dict[str, list[float]]):
    rows = []
    for t, closes in prices.items():
        for d, c in zip(DAYS[: len(closes)], closes, strict=True):
            rows.append({"date": d, "ticker": t, "market": "KR", "close": c, "volume": 1})
    df = pd.DataFrame(rows)
    for c in ml_api.FEATURE_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    ml_api.write_rows(df[ml_api.FEATURE_COLUMNS])


def _at(d: date, hour: int = 1) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=UTC)


def _decision_row(conn, ticker, action, d):
    ingest_checkpoint(
        conn, d, "KR", ticker, "30_decision", {"data": ok_decision(ticker=ticker, action=action)}
    )
    return kledger.decision_id(conn, d, "KR", ticker)


def _record(conn, dec_id, ticker, action, d, *, round_no=0, hour=1, held=None):
    return store.record_decision(
        conn,
        decision_id=dec_id,
        ticker=ticker,
        market="KR",
        account_id="paper-main",
        decided_at=_at(d, hour).isoformat(),
        decision={"action": action, "confidence": 0.6},
        row=None,
        horizon_days=5,
        vector=[0.5] * 12,
        round_no=round_no,
        held_at_decision=held,
    )


def _snapshot(conn, d, ticker, qty):
    kledger.snapshot_positions(
        conn,
        account_id="paper-main",
        d=d,
        nav=Decimal("10000000"),
        cash=Decimal("5000000"),
        positions=[
            {
                "ticker": ticker,
                "market": "KR",
                "qty": qty,
                "avg_price": Decimal("100"),
                "current_price": Decimal("100"),
                "eval_amount": Decimal(100 * qty),
            }
        ],
        market="KR",
    )


# --- L2: 재평가는 별도 엔트리 ---


def test_reevaluation_keeps_morning_entry_and_adds_round_entry(conn):
    dec_id = _decision_row(conn, "A", "BUY", DAYS[0])
    morning = _record(conn, dec_id, "A", "BUY", DAYS[0])
    reeval = _record(conn, dec_id, "A", "SELL", DAYS[0], round_no=1, hour=4)
    assert morning != reeval
    m, r = store.get_entry(conn, morning), store.get_entry(conn, reeval)
    assert m["decision"]["action"] == "BUY" and m["round"] == 0
    assert r["decision"]["action"] == "SELL" and r["round"] == 1
    # 같은 라운드의 재기록은 멱등 (세션 재시도)
    assert _record(conn, dec_id, "A", "SELL", DAYS[0], round_no=1, hour=5) == reeval
    assert conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0] == 2


def test_matured_count_and_retrieve_prefer_round0(conn):
    for i in range(3):
        dec_id = _decision_row(conn, f"T{i}", "BUY", DAYS[i])
        e0 = _record(conn, dec_id, f"T{i}", "BUY", DAYS[i])
        e1 = _record(conn, dec_id, f"T{i}", "TRIM", DAYS[i], round_no=1, hour=4)
        for e in (e0, e1):
            store.fill_outcome(
                conn, e, outcome_at=NOW.isoformat(), price_return=0.01, direction_score=0.01
            )
    assert store.matured_count(conn) == 3  # 결정 수(아침 라운드)만 센다
    r = store.retrieve(
        conn,
        vector=[0.5] * 12,
        now=NOW,
        settings=MemorySettings(warmup=1, top_k=6),
        market="KR",
    )
    rounds = [e["round"] for e in r["entries"]]
    assert rounds[:3] == [0, 0, 0] and rounds[3:] == [1, 1, 1]
    by_round = {e["round"]: e["score"] for e in r["entries"]}
    assert by_round[1] < by_round[0]


# --- L3: 채점 ---


def test_hold_scored_by_holding_at_decision(conn, project):
    _store({"A": [100] * 5 + [90] * 25, "B": [100] * 5 + [110] * 25})
    a = _record(conn, _decision_row(conn, "A", "HOLD", DAYS[0]), "A", "HOLD", DAYS[0], held=True)
    b = _record(conn, _decision_row(conn, "B", "HOLD", DAYS[0]), "B", "HOLD", DAYS[0], held=False)
    c = _record(conn, _decision_row(conn, "A", "HOLD", DAYS[1]), "A", "HOLD", DAYS[1], held=False)
    outcomes.mature(conn, state=None, today=DAYS[10])
    ea, eb, ec = (store.get_entry(conn, x) for x in (a, b, c))
    # 보유 HOLD: 계속 들고 있었으니 가격수익 그대로
    assert ea["direction_score"] == pytest.approx(-0.10) and ea["avoided_loss"] is None
    # 미보유 HOLD: 점수 0, 오른 종목을 안 산 것은 손실이 아니다(회피 손실 0)
    assert eb["direction_score"] == 0.0 and eb["avoided_loss"] == 0.0
    # 미보유 HOLD 가 하락을 피했으면 회피 손실
    assert ec["direction_score"] == 0.0 and ec["avoided_loss"] == pytest.approx(0.10)
    assert eb["price_return"] == pytest.approx(0.10)


def test_hold_held_falls_back_to_positions_snapshot(conn, project):
    _store({"A": [100] * 5 + [110] * 25})
    _snapshot(conn, DAYS[0], "A", 10)
    e = _record(conn, _decision_row(conn, "A", "HOLD", DAYS[0]), "A", "HOLD", DAYS[0])
    outcomes.mature(conn, state=None, today=DAYS[10])
    assert store.get_entry(conn, e)["direction_score"] == pytest.approx(0.10)


def test_sell_scores_avoided_loss_not_short_profit(conn, project):
    _store({"A": [100] * 5 + [90] * 25, "B": [100] * 5 + [110] * 25})
    s1 = _record(conn, _decision_row(conn, "A", "SELL", DAYS[0]), "A", "SELL", DAYS[0])
    s2 = _record(conn, _decision_row(conn, "B", "TRIM", DAYS[0]), "B", "TRIM", DAYS[0])
    outcomes.mature(conn, state=None, today=DAYS[10])
    e1, e2 = store.get_entry(conn, s1), store.get_entry(conn, s2)
    assert e1["direction_score"] == pytest.approx(0.10) and e1["avoided_loss"] == pytest.approx(
        0.10
    )
    assert e2["direction_score"] == pytest.approx(-0.10) and e2["avoided_loss"] == 0.0


def test_buy_has_no_avoided_loss(conn, project):
    _store({"A": [100] * 5 + [104] * 25})
    e = _record(conn, _decision_row(conn, "A", "BUY", DAYS[0]), "A", "BUY", DAYS[0])
    outcomes.mature(conn, state=None, today=DAYS[10])
    x = store.get_entry(conn, e)
    assert x["direction_score"] == pytest.approx(0.04) and x["avoided_loss"] is None
    assert x["realized_pnl_pct"] is None and x["post_fill_return"] is None  # 체결이 없으면 NULL


def _order_with_fill(conn, dec_id, side, created, fill_price, qty=10):
    oid = kledger.insert_order(
        conn,
        decision_id=dec_id,
        account_id="paper-main",
        ticker="A",
        market="KR",
        side=side,
        qty=qty,
        order_type="LIMIT",
        price=Decimal(str(fill_price)),
        status="filled",
        order_no="1",
        message="",
        mode="auto",
        created_at=created.isoformat(),
    )
    kledger.insert_fill(
        conn,
        order_id=oid,
        account_id="paper-main",
        market="KR",
        ticker="A",
        side=side,
        qty=qty,
        price=Decimal(str(fill_price)),
        filled_at=created.isoformat(),
        order_no=None,
    )
    return oid


def test_realized_pnl_uses_actual_fill_price_per_round(conn, project):
    # 결정일 종가 100, 만기 종가 110. 아침 BUY 는 102 에 체결, 재평가 TRIM 은 108 에 체결
    _store({"A": [100] * 5 + [110] * 25})
    dec_id = _decision_row(conn, "A", "BUY", DAYS[0])
    morning = _record(conn, dec_id, "A", "BUY", DAYS[0], hour=1)
    _order_with_fill(conn, dec_id, "BUY", _at(DAYS[0], 1) + timedelta(seconds=5), 102)
    reeval = _record(conn, dec_id, "A", "TRIM", DAYS[0], round_no=1, hour=4)
    _order_with_fill(conn, dec_id, "SELL", _at(DAYS[0], 4) + timedelta(seconds=5), 108)
    outcomes.mature(conn, state=None, today=DAYS[10])
    m, r = store.get_entry(conn, morning), store.get_entry(conn, reeval)
    # 2-L8: 체결가 → 만기 종가 수익은 post_fill_return(매수는 보유 성과, 매도는 회피 성과)
    assert m["post_fill_return"] == pytest.approx(110 / 102 - 1)
    # TRIM: 판 가격 108 → 만기 110 이면 판 뒤 더 올랐으니 음수
    assert r["post_fill_return"] == pytest.approx(-(110 / 108 - 1))
    # realized_pnl_pct 는 SELL/TRIM 의 실제 실현손익(FIFO 원가 102 → 108, 왕복비용 30bp 차감)
    assert r["realized_pnl_pct"] == pytest.approx(108 / 102 - 1 - 0.003)
    assert m["realized_pnl_pct"] is None  # 매수는 실현이 아니다
    assert m["direction_score"] == pytest.approx(0.10) and r["direction_score"] == pytest.approx(
        -0.10
    )


def test_approved_order_belongs_to_round_that_requested_it(conn, project):
    """Codex: 승인 주문은 승인 시각에 생긴다 — 요청한 라운드(아침)로 돌려야 한다."""
    _store({"A": [100] * 5 + [110] * 25})
    dec_id = _decision_row(conn, "A", "BUY", DAYS[0])
    morning = _record(conn, dec_id, "A", "BUY", DAYS[0], hour=1)
    reeval = _record(conn, dec_id, "A", "ADD", DAYS[0], round_no=1, hour=2)
    requested = _at(DAYS[0], 1) + timedelta(seconds=5)
    approved = _at(DAYS[0], 3)  # 재평가 뒤 승인
    conn.execute(
        "INSERT INTO approvals(decision_id, account_id, order_json, status, requested_at, "
        "expires_at, decided_at, fingerprint) "
        "VALUES (?, 'paper-main', '{}', 'approved', ?, ?, ?, 'fp')",
        (dec_id, requested.isoformat(), approved.isoformat(), approved.isoformat()),
    )
    oid = _order_with_fill(conn, dec_id, "BUY", approved + timedelta(seconds=1), 104)
    conn.execute("UPDATE orders SET mode='approve' WHERE id=?", (oid,))
    conn.commit()
    outcomes.mature(conn, state=None, today=DAYS[10])
    assert store.get_entry(conn, morning)["post_fill_return"] == pytest.approx(110 / 104 - 1)
    assert store.get_entry(conn, reeval)["post_fill_return"] is None


def test_sell_without_known_cost_basis_has_no_realized_pnl(conn, project):
    """2-L8: 원장 밖에서 산 종목(원가 불명)을 판 SELL 은 실현손익을 지어내지 않는다(null)."""
    _store({"A": [100] * 5 + [110] * 25})
    dec_id = _decision_row(conn, "A", "SELL", DAYS[0])
    e = _record(conn, dec_id, "A", "SELL", DAYS[0], hour=1)
    _order_with_fill(conn, dec_id, "SELL", _at(DAYS[0], 1) + timedelta(seconds=5), 108)
    outcomes.mature(conn, state=None, today=DAYS[10])
    got = store.get_entry(conn, e)
    assert got["realized_pnl_pct"] is None and got["post_fill_return"] == pytest.approx(
        -(110 / 108 - 1)
    )
