"""kernel/execution/ledger.latest_positions — 최신 스냅샷 조회."""

from datetime import date
from decimal import Decimal

from trader.kernel.execution import ledger as xledger
from trader.kernel.execution.ledger import latest_positions


def test_latest_positions_returns_latest_snapshot_rows(conn):
    assert latest_positions(conn, "acct-a", "KR") is None
    xledger.snapshot_positions(
        conn, account_id="acct-a", d=date(2026, 10, 1), market="KR",
        nav=Decimal("100"), cash=Decimal("10"),
        positions=[{"ticker": "000001", "market": "KR", "qty": 1}],
    )  # fmt: skip
    xledger.snapshot_positions(
        conn, account_id="acct-a", d=date(2026, 10, 2), market="KR",
        nav=Decimal("200"), cash=Decimal("20"),
        positions=[
            {"ticker": "000002", "market": "KR", "qty": 2},
            {"ticker": "000001", "market": "KR", "qty": 3},
        ],
    )  # fmt: skip
    latest = latest_positions(conn, "acct-a", "KR")
    assert latest["date"] == "2026-10-02"
    assert (latest["nav"], latest["cash"]) == ("200", "20")
    assert [(p["ticker"], p["qty"]) for p in latest["positions"]] == [
        ("000001", 3),
        ("000002", 2),
    ]
    assert latest_positions(conn, "acct-a", "US") is None


def test_latest_positions_filters_asset(conn):
    for asset, ticker in (("stock", "005930"), ("fo", "A0565")):
        xledger.snapshot_positions(
            conn, account_id="paper-main", d=date(2026, 10, 2), market="KR",
            nav=Decimal("100"), cash=Decimal("10"), asset=asset,
            positions=[{"ticker": ticker, "market": "KR", "qty": 1}],
        )  # fmt: skip
    stock = latest_positions(conn, "paper-main", "KR")
    assert [p["ticker"] for p in stock["positions"]] == ["005930"]
    fo = latest_positions(conn, "paper-main", "KR", asset="fo")
    assert [p["ticker"] for p in fo["positions"]] == ["A0565"]


def test_snapshot_keeps_settlement_aware_cash(conn):
    """예수금과 주문 가능 금액(결제 예정 반영)을 따로 남기고, 같은 날 다시 쓰면 갱신한다."""
    args = {"account_id": "a", "d": date(2026, 10, 2), "market": "KR", "positions": []}
    xledger.snapshot_positions(
        conn, nav=Decimal("100"), cash=Decimal("100"), cash_available=Decimal("80"), **args
    )
    assert latest_positions(conn, "a", "KR")["cash_available"] == "80"
    xledger.snapshot_positions(
        conn, nav=Decimal("100"), cash=Decimal("100"), cash_available=Decimal("70"), **args
    )
    assert latest_positions(conn, "a", "KR")["cash_available"] == "70"


def test_snapshot_cash_available_is_optional(conn):
    xledger.snapshot_positions(
        conn, account_id="a", d=date(2026, 10, 2), market="KR",
        nav=Decimal("100"), cash=Decimal("100"), positions=[],
    )  # fmt: skip
    assert latest_positions(conn, "a", "KR")["cash_available"] is None
