"""ports["evaluation"] 의 메모리·보정·브레이커 부분."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.features.accounts.conftest import make_account
from tests.features.evaluation.helpers import seed_matured
from tests.features.spot.helpers import ok_decision
from trader.features.evaluation.memory import store
from trader.features.evaluation.ports import EvaluationPortImpl
from trader.features.spot.ledger import ingest_checkpoint
from trader.kernel.execution import ledger as kledger

NOW = datetime(2026, 10, 17, tzinfo=UTC)


class _Ml:
    def __init__(self, row=None):
        self.row = row
        self.calls: list = []

    def latest_row(self, ticker, market, *, asof):
        self.calls.append((ticker, market, asof))
        return self.row


def _port(settings, ml=None):
    return EvaluationPortImpl(settings, None, {"ml": ml or _Ml()})


def _ctx_kwargs(now=NOW):
    return {
        "inputs": {"meta": {"ticker": "T000"}},
        "market": "KR",
        "asof_date": date(2026, 10, 17),
        "now": now,
        "state": None,
    }


def test_memory_context_warmup_without_entries(conn, settings):
    out = _port(settings).memory_context(conn, **_ctx_kwargs())
    assert out == {"memory": [], "memory_status": {"warmup": True, "breaker": None}}


def test_memory_context_survives_bad_vectors_and_naive_now(conn, settings):
    """저장 벡터 차원이 질의 벡터와 다르고 now 가 naive 여도 예외 없이 error 로 돌려준다."""
    seed_matured(conn, 55, vec=[0.5] * 3, now=NOW)
    out = _port(settings).memory_context(conn, **_ctx_kwargs(now=datetime(2026, 10, 17)))
    assert out["memory"] == [] and out["memory_status"]["warmup"] is True
    assert out["memory_status"]["error"]


def test_memory_context_returns_entries_and_records_vix(conn, settings):
    seed_matured(conn, 55, now=NOW)
    ml = _Ml()
    inputs = {
        "meta": {"ticker": "T000"},
        "news_macro": {"macro": {"vix": {"data": {"value": 20.0, "date": "2026-10-16"}}}},
    }
    out = _port(settings, ml).memory_context(
        conn, **{**_ctx_kwargs(), "inputs": inputs, "asof_date": date(2026, 10, 17)}
    )
    assert len(out["memory"]) == settings.memory.top_k
    assert out["memory_status"] == {"warmup": False, "breaker": None}
    assert ml.calls == [("T000", "KR", date(2026, 10, 17))]  # A12: asof=당일
    assert conn.execute("SELECT COUNT(*) FROM control WHERE key LIKE 'vix_last%'").fetchone()[0]


def test_record_decision_marks_held_and_decided_at_close_for_backfill(conn, settings):
    d = date(2026, 9, 1)
    ingest_checkpoint(conn, d, "KR", "A", "30_decision", {"data": ok_decision(ticker="A")})
    kledger.snapshot_positions(
        conn,
        account_id="paper-main",
        d=d,
        nav=Decimal("10000000"),
        cash=Decimal("5000000"),
        positions=[
            {
                "ticker": "A",
                "market": "KR",
                "qty": 3,
                "avg_price": Decimal("100"),
                "current_price": Decimal("100"),
                "eval_amount": Decimal(300),
            }
        ],
        market="KR",
    )
    accounts = [
        make_account("paper-main", "paper", "auto", markets=["KR"]),
        make_account("real-1", "real", "approve", markets=["KR"]),
    ]
    eid = _port(settings).record_decision(
        conn,
        decision=ok_decision(ticker="A"),
        ticker="A",
        market="KR",
        date=d,
        round_no=0,
        now=NOW,  # 운영일(10-17)이 d 와 다르다 → 백필
        state=None,
        accounts=accounts,
        skipped_debate=True,
    )
    e = store.get_entry(conn, eid)
    assert e["account_id"] == "paper-main" and e["held_at_decision"] in (1, True)
    assert e["decided_at"] == "2026-09-01T06:30:00+00:00"  # 15:30 KST
    assert e["skipped_debate"] in (1, True)
    # 결정 행이 없으면 None
    assert (
        _port(settings).record_decision(
            conn, decision=ok_decision(ticker="Z"), ticker="Z", market="KR", date=d,
            round_no=0, now=NOW, state=None, accounts=accounts, skipped_debate=None,
        )
        is None
    )


def test_calibration_report_none_when_not_injected(conn, settings):
    seed_matured(conn, 60)
    port = _port(settings)
    assert port.calibration_injected() is False  # ml.yaml 기본 inject=false
    assert (
        port.calibration_report(
            conn, ticker="T000", market="KR", asof_date=date(2026, 10, 17), state=None,
            now_iso=NOW.isoformat(),
        )
        is None
    )
    assert conn.execute("SELECT COUNT(*) FROM calibration_runs").fetchone()[0] == 0


def test_calibration_report_built_when_injected(conn, settings, monkeypatch):
    from trader.features.evaluation import ports as ports_mod
    from trader.features.ml import api as ml_api

    cfg = ml_api.ml_config({"models": {"calibration": {"enabled": True, "inject": True}}})
    monkeypatch.setattr(ports_mod.ml_api, "load_ml", lambda registry=None: cfg)
    seed_matured(conn, 60)
    port = _port(settings)
    assert port.calibration_injected() is True
    r = port.calibration_report(
        conn, ticker="T000", market="KR", asof_date=date(2026, 10, 17), state=None,
        now_iso=NOW.isoformat(),
    )
    assert r["active"] is True and r["n_decisions"] == 60


def test_update_breaker_writes_market_flag(conn, settings):
    nav = [("2026-10-15", Decimal("1200")), ("2026-10-16", Decimal("1000"))]
    out = _port(settings).update_breaker(conn, market="KR", nav_series=nav)
    assert out is not None and out["active"] is True
    assert store.breaker_state(conn, "KR")["active"] is True
    assert conn.execute(
        "SELECT COUNT(*) FROM control WHERE key='memory_breaker:KR'"
    ).fetchone()[0] == 1


def test_rl_sizer_is_none_until_task_11(settings):
    assert _port(settings).rl_sizer(None) is None


def test_build_port_registers_evaluation(settings):
    from trader.features.evaluation.feature import FEATURE

    assert [p.name for p in FEATURE.ports] == ["evaluation"]
    pytest.importorskip("trader")


def test_held_at_sees_empty_account_nav_snapshot(conn):
    """원장 v5 빈 계좌는 account_nav 만 남는다 — 보유 없음(False)이지 모름(None)이 아니다."""
    assert store.held_at(conn, account_id="p", market="KR", ticker="A", d="2026-09-01") is None
    kledger.snapshot_positions(
        conn, account_id="p", d=date(2026, 9, 1), nav=Decimal(100), cash=Decimal(100),
        positions=[], market="KR",
    )  # fmt: skip
    assert store.held_at(conn, account_id="p", market="KR", ticker="A", d="2026-09-01") is False


def test_held_at_ignores_same_ticker_in_other_market(conn):
    """두 번째 조회도 시장을 건다 — 같은 날 다른 시장 스냅샷의 같은 티커를 보유로 보지 않는다."""
    pos = {"ticker": "A", "qty": 3, "avg_price": 1, "current_price": 1, "eval_amount": 3}
    kledger.snapshot_positions(
        conn, account_id="p", d=date(2026, 9, 1), nav=Decimal(100), cash=Decimal(97),
        positions=[pos], market="US",
    )  # fmt: skip
    kledger.snapshot_positions(
        conn, account_id="p", d=date(2026, 9, 1), nav=Decimal(100), cash=Decimal(100),
        positions=[], market="KR",
    )  # fmt: skip
    assert store.held_at(conn, account_id="p", market="US", ticker="A", d="2026-09-01") is True
    assert store.held_at(conn, account_id="p", market="KR", ticker="A", d="2026-09-01") is False
