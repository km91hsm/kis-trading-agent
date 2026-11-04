"""Phase 26: 성과·로트·메모리·준비도가 파생 계좌를 `asset` 로 가른다(옛 test_performance_fo 이식).

옛 `test_account_summary_and_asset` 2건 중 `views.account_summary`·`repo.account_asset` 의존분은
evaluation 에 대응 함수가 없어 이식하지 않았다(웹 뷰는 M11)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from tests.features.accounts.conftest import make_account
from trader.features.evaluation import lots, performance, readiness
from trader.features.evaluation.memory import store as memory_store
from trader.kernel.execution import ledger as repo

ACCT = "paper-fo"


def fo_account():
    return make_account(ACCT, "paper", "auto", asset="fo")


def _navs(conn, pairs):
    for d, v in pairs:
        conn.execute(
            "INSERT OR REPLACE INTO fo_nav_snapshot(account_id, date, nav, cash, margin_total, "
            "add_margin, created_at) VALUES (?, ?, ?, ?, '0', '0', 't')",
            (ACCT, d, None if v is None else str(v), None if v is None else str(v)),
        )
    conn.commit()


def test_nav_series_fo_adapter(conn):
    # 10-23 금 · 10-24 토(제외, 현물 nav_history 와 같은 거래일 표본) · 10-27 화 · 10-28(nav 없음)
    _navs(conn, [("2026-10-23", "100"), ("2026-10-24", "101"), ("2026-10-27", "102"),
                 ("2026-10-28", None)])  # fmt: skip
    s = performance.nav_series(conn, ACCT, "KR", days=30, asset="fo")
    assert s == [("2026-10-23", Decimal("100")), ("2026-10-27", Decimal("102"))]
    assert performance.nav_series(conn, ACCT, "KR", days=30) == []  # 현물 경로엔 없다
    rep = performance.report(conn, None, ACCT, "KR", days=30, asset="fo")
    assert rep["metrics"]["return"] == 0.02 and rep["metrics"]["days"] == 2


def test_nav_series_fo_days_window_and_empty(conn):
    assert performance.nav_series(conn, ACCT, "KR", days=30, asset="fo") == []
    _navs(conn, [("2026-09-01", "90"), ("2026-10-15", "100"), ("2026-10-27", "102")])
    s = performance.nav_series(conn, ACCT, "KR", days=14, asset="fo")  # 마지막 날짜 기준 14일
    assert [d for d, _ in s] == ["2026-10-15", "2026-10-27"]


def test_report_fo_empty_when_no_snapshot(conn):
    rep = performance.report(conn, None, ACCT, "KR", days=30, asset="fo")
    assert rep["metrics"]["return"] is None


def test_lots_fo_empty(conn):
    """파생 체결은 로트(FIFO·세금) 대상이 아니다 — 빈 결과."""
    oid = repo.insert_order(
        conn, decision_id=None, account_id=ACCT, ticker="105W11", market="KR", side="BUY",
        qty=2, order_type="LIMIT", price=Decimal("350"), status="filled", order_no="A",
        message="", mode="auto", asset="fo",
    )  # fmt: skip
    repo.insert_fill(
        conn, order_id=oid, account_id=ACCT, market="KR", ticker="105W11", side="BUY", qty=2,
        price=Decimal("350"), filled_at="2026-10-27T01:30:00+00:00", order_no="A",
    )  # fmt: skip
    assert lots.load_fills(conn, ACCT, "KR") == []


def test_memory_held_at_none_for_fo(conn):
    assert (
        memory_store.held_at(conn, account_id=ACCT, market="KR", ticker="105W11", d="2026-10-27")
        is None
    )


def test_readiness_skips_fo(conn, project: Path):
    _navs(conn, [("2026-10-23", "100"), ("2026-10-27", "90")])
    out = readiness.check(conn, accounts=[fo_account()], state=project / "state",
                          today=date(2026, 10, 27))  # fmt: skip
    items = {i["key"]: i for i in out["items"]}
    # 파생 NAV −10% 가 준비도에 섞이지 않는다(값 없음 = 평가 대상 계좌 없음)
    assert items["return_vs_benchmark"]["value"] is None
    assert items["mdd"]["value"] is None
