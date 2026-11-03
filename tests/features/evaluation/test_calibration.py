"""보정 리포트(신호 적중률·결정 품질) — 옛 calibration/model 이식 + A3(price_return 만 사용)."""

from datetime import UTC, date, datetime

import pytest

from tests.features.evaluation.helpers import seed_matured
from tests.features.spot.helpers import ok_decision, ok_report
from trader.features.evaluation import calibration as model
from trader.features.evaluation.memory import store
from trader.features.ml import api as ml_api
from trader.features.spot.ledger import ingest_checkpoint
from trader.kernel.execution.ledger import decision_id

NOW = datetime(2026, 10, 17, tzinfo=UTC)


def test_signal_hits_join_and_sign_mapping(conn):
    seed_matured(conn, 10, hit_rate=0.7)
    df = model.signal_hits(conn)
    assert len(df) == 40 and set(df["role"]) == {
        "technical",
        "fundamental",
        "news_macro",
        "sentiment",
    }
    assert set(df["signal"]) == {1, -1} and set(df["realized_sign"]) == {1, -1}
    assert df[df["role"] == "sentiment"]["signal"].eq(-1).all()
    assert len(df["vector"].iloc[0]) == 12


def test_hit_rates_overall_and_neighbors(conn):
    seed_matured(conn, 20, hit_rate=0.7)
    df = model.signal_hits(conn)
    r = model.hit_rates(df, query_vector=[0.5] * 12, k=20)
    assert abs(r["technical"]["hit_rate"] - 0.7) < 1e-9 and r["technical"]["n"] == 20
    assert abs(r["sentiment"]["hit_rate"] - 0.3) < 1e-9  # SELL 신호는 상승 때 빗나감
    assert (
        r["technical"]["neighbor_n"] == 20 and abs(r["technical"]["neighbor_hit_rate"] - 0.7) < 1e-9
    )
    assert r["sentiment"]["bias"] < 0 < r["technical"]["bias"]
    r2 = model.hit_rates(df, query_vector=None)
    assert r2["technical"]["neighbor_hit_rate"] is None


def test_calibration_inactive_below_60(conn):
    seed_matured(conn, 59)
    assert (
        model.build_calibration(conn, min_decisions=60, query_vector=None, asof=NOW.isoformat())
        is None
    )
    assert conn.execute("SELECT active FROM calibration_runs").fetchone()["active"] == 0


def test_calibration_active_at_60(conn):
    seed_matured(conn, 60, hit_rate=0.6)
    r = model.build_calibration(
        conn, min_decisions=60, query_vector=[0.5] * 12, asof=NOW.isoformat()
    )
    assert (
        r["active"] is True
        and r["n_decisions"] == 60
        and set(r["roles"]) >= {"technical", "sentiment"}
    )
    assert abs(r["roles"]["technical"]["hit_rate"] - 0.6) < 1e-9
    row = conn.execute(
        "SELECT active, n_decisions FROM calibration_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["active"] == 1 and row["n_decisions"] == 60
    assert ml_api.ml_config({}).models.calibration.min_decisions == 60


def test_signal_hits_uses_price_return_for_hold(conn):
    """HOLD 결정은 실현수익이 0 이지만 가격 방향(price_return)으로 신호를 채점한다."""
    seed_matured(conn, 10, action="HOLD", hit_rate=1.0)
    df = model.signal_hits(conn)
    assert len(df) == 40 and (df["realized_sign"] == 1).all()
    rates = model.hit_rates(df, query_vector=None)
    assert rates["technical"]["hit_rate"] == pytest.approx(1.0)
    assert rates["sentiment"]["hit_rate"] == pytest.approx(0.0)  # SELL 신호는 빗나감


def test_build_calibration_one_row_per_day(conn):
    seed_matured(conn, 3)
    for _ in range(2):
        model.build_calibration(
            conn, min_decisions=1, query_vector=None, asof="2026-10-17T00:00:00+00:00"
        )
    assert conn.execute("SELECT COUNT(*) FROM calibration_runs").fetchone()[0] == 1


def test_build_calibration_day_key_is_kst(conn):
    seed_matured(conn, 3)
    # 10-17 23:30Z = 10-18 08:30 KST, 10-18 02:00Z = 10-18 11:00 KST → 같은 KST 날: 1행
    for now in (datetime(2026, 10, 17, 23, 30, tzinfo=UTC), datetime(2026, 10, 18, 2, tzinfo=UTC)):
        model.build_calibration(
            conn, min_decisions=1, query_vector=None, asof=now.isoformat(), now=lambda n=now: n
        )
    assert conn.execute("SELECT COUNT(*) FROM calibration_runs").fetchone()[0] == 1
    model.build_calibration(
        conn,
        min_decisions=1,
        query_vector=None,
        asof="x",
        now=lambda: datetime(2026, 10, 17, 10, tzinfo=UTC),  # 10-17 19:00 KST → 다른 날
    )
    assert conn.execute("SELECT COUNT(*) FROM calibration_runs").fetchone()[0] == 2


def test_signal_hits_single_query(conn):
    """만기 엔트리마다 analyst_reports 를 한 번씩 조회(N+1)하지 않고 JOIN 1회로 같은 결과를 낸다."""
    seed_matured(conn, 12, hit_rate=0.6)
    stmts: list[str] = []
    conn.set_trace_callback(stmts.append)
    df = model.signal_hits(conn)
    conn.set_trace_callback(None)
    assert len(df) == 48
    assert sum("analyst_reports" in s for s in stmts) == 1


def _entry(conn, ticker, action, d, *, round_no=0, hour=1):
    ingest_checkpoint(
        conn, d, "KR", ticker, "30_decision", {"data": ok_decision(ticker=ticker, action=action)}
    ) if round_no == 0 else None
    return store.record_decision(
        conn,
        decision_id=decision_id(conn, d, "KR", ticker),
        ticker=ticker,
        market="KR",
        account_id="paper-main",
        decided_at=datetime(d.year, d.month, d.day, hour, tzinfo=UTC).isoformat(),
        decision={"action": action, "confidence": 0.6},
        row=None,
        horizon_days=5,
        vector=[0.5] * 12,
        round_no=round_no,
    )


def test_decision_quality_caps_reevaluation_weight_per_decision(conn):
    # 결정 1: 아침 +, 재평가 10건 전부 −. 결정 2: 아침 −. 재평가가 결정당 합 0.5 라
    # 적중률 = (1) / (1 + 0.5 + 1) = 0.4
    d0 = date(2026, 9, 1)
    entries = [(_entry(conn, "A", "BUY", d0), 0.02)]
    for n in range(1, 11):
        entries.append((_entry(conn, "A", "ADD", d0, round_no=n, hour=2), -0.01))
    entries.append((_entry(conn, "B", "BUY", d0), -0.02))
    for e, ds in entries:
        store.fill_outcome(
            conn, e, outcome_at=NOW.isoformat(), price_return=ds, direction_score=ds
        )
    q = model.decision_quality(conn)
    assert q["n"] == 12 and q["hit_rate"] == pytest.approx(0.4)


def test_us_calibration_and_backtest_use_decision_run_date_not_kst_date(conn):
    """US 결정이 KST 자정 뒤 실제 시각으로 남아도 보정·백테스트는 운영일로 묶는다.
    (복기 `reflect.pending` 소비자는 Task 10 에서 같은 시나리오로 추가한다.)"""
    from trader.features.evaluation import backtest

    run = date(2026, 10, 26)
    ingest_checkpoint(conn, run, "US", "AAPL", "30_decision", {"data": ok_decision(ticker="AAPL")})
    dec_id = decision_id(conn, run, "US", "AAPL")
    ingest_checkpoint(
        conn,
        run,
        "US",
        "AAPL",
        "10_analyst_technical",
        {"data": ok_report(role="technical", ticker="AAPL", signal="BUY")},
    )
    late = datetime(2026, 10, 26, 16, 0, tzinfo=UTC)  # 10-27 01:00 KST
    e = store.record_decision(
        conn,
        decision_id=dec_id,
        ticker="AAPL",
        market="US",
        account_id="paper-main",
        decided_at=late.isoformat(),
        decision={"action": "BUY", "confidence": 0.6},
        row=None,
        horizon_days=5,
        vector=[0.5] * 12,
    )
    store.fill_outcome(
        conn, e, outcome_at=NOW.isoformat(), price_return=0.02, direction_score=0.02
    )
    hits = model.signal_hits(conn)
    assert list(hits["role"]) == ["technical"] and list(hits["realized_sign"]) == [1]
    bt = backtest.simple_backtest(conn, None, since=run, market="US")
    assert bt["n"] == 1


def test_signal_hits_excludes_rows_without_price_return(conn):
    """A3: price_return 이 NULL 인 만기 행은 채점에서 빠진다."""
    ids = seed_matured(conn, 4)
    conn.execute("UPDATE memory_entries SET price_return=NULL WHERE id=?", (ids[0],))
    conn.commit()
    assert set(model.signal_hits(conn)["decision_id"]) == set(ids[1:])
