"""장 마감 일일 리포트(Stock D-095): 계좌·시장별 NAV·전일 대비·최근 5일 실현손익·주문/체결·미결·
킬스위치 한 줄. 오늘 NAV 행이 없는 계좌·시장은 뺀다."""

from datetime import date
from decimal import Decimal

from tests.features.accounts.conftest import make_account
from trader.features.evaluation import daily_report
from trader.features.regime import killswitch
from trader.kernel.execution import ledger as repo

PAPER = make_account("paper-main", "paper", "auto", ["KR", "US"])
D = date(2026, 10, 27)


def _snap(conn, d, nav, ticker="", qty=0, market="KR"):
    positions = [{"ticker": ticker, "qty": qty}] if ticker else []
    repo.snapshot_positions(
        conn, account_id="paper-main", d=date.fromisoformat(d), nav=Decimal(nav),
        cash=Decimal(0), positions=positions, market=market,
    )  # fmt: skip


def _order(conn, status, at="2026-10-27T01:00:00+00:00"):
    repo.insert_order(
        conn, decision_id=None, account_id="paper-main", ticker="005930", market="KR", side="BUY",
        qty=1, order_type="limit", price=Decimal(10000), status=status, order_no=None,
        message="", mode="auto", created_at=at,
    )  # fmt: skip


def test_report_rows_and_message(conn):
    _snap(conn, "2026-10-24", 10_000_000)
    _snap(conn, "2026-10-27", 10_150_000, "005930", 3)
    # 같은 날 두 번째 종목: 스냅샷은 종목 단위 upsert 라 직접 적재한다
    conn.execute(
        "INSERT INTO positions_snapshot(account_id, date, market, asset, ticker, qty, created_at)"
        " VALUES ('paper-main','2026-10-27','KR','stock','000660',1,'2026-10-27T07:00:00+00:00')"
    )
    conn.commit()
    _order(conn, "filled")
    _order(conn, "accepted")
    _order(conn, "accepted", at="2026-10-20T01:00:00+00:00")  # 지난 주문은 오늘 집계 밖
    r = daily_report.compute(conn, [PAPER], markets=["KR", "US"], today_of=lambda m: D)
    (row,) = r["items"]  # US 는 오늘 NAV 가 없어 빠진다
    assert row["market"] == "KR" and row["holdings"] == 2
    assert abs(row["change_pct"] - 0.015) < 1e-9
    assert row["orders"] == {"filled": 1, "accepted": 1} and row["open"] == 2
    text = daily_report.message(r)
    assert text.startswith("[일일 리포트]") and "paper-main KR" in text and "+1.50%" in text
    killswitch.engage(conn, reason="테스트", source="cli")
    assert "킬스위치" in daily_report.message(
        daily_report.compute(conn, [PAPER], markets=["KR"], today_of=lambda m: D)
    )


def test_empty_when_no_snapshots(conn):
    r = daily_report.compute(conn, [PAPER], markets=["KR"], today_of=lambda m: D)
    assert r["items"] == []


def test_daily_report_cash_only_day_reports_zero_holdings(conn):
    """보유 0(전량 현금) 날도 NAV 행이 있으면 리포트에 나오고 보유는 0 (Review Focus 4)."""
    _snap(conn, "2026-10-27", 5_000_000)
    r = daily_report.compute(conn, [PAPER], markets=["KR"], today_of=lambda m: D)
    (row,) = r["items"]
    assert row["holdings"] == 0 and row["nav"] == "5000000" and row["change_pct"] is None
