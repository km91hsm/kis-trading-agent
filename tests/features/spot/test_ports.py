"""spot 포트 `stock_exec`: 결정 행 없는 파이썬 주문(단타)을 spot executor·코어로 낸다."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.features.spot.conftest import T0, D, Notes, make_account
from trader.features.accounts import api as accounts_api
from trader.features.accounts.settings import to_broker_account
from trader.features.spot import ports as spot_ports
from trader.features.spot.feature import FEATURE
from trader.features.spot.ports import StockExecPortImpl
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash, Fill
from trader.kernel.execution import ledger as repo
from trader.kernel.session.run_store import run_dir

NAV = Decimal("10000000")  # 1천만원 — 0.05 비중 / 지정가 10050 → 49주


def _decision(**over) -> dict:
    d = {
        "ticker": "005930",
        "action": "BUY",
        "target_weight": 0.05,
        "order_type": "LIMIT",
        "limit_price": 10050.0,
        "horizon_days": 1,
        "confidence": 1.0,
        "rationale": "분 단위 단타(진입): 테스트",
        "invalidation_conditions": [],
    }
    d.update(over)
    return d


@pytest.fixture
def broker():
    acct = make_account("paper-main", "paper", "auto", markets=["KR"])
    b = FakeBroker(
        to_broker_account(acct),
        cash={"KR": Cash(currency="KRW", available=NAV, total=NAV)},
    )
    return acct, b


@pytest.fixture
def exec_ports(ports, broker):
    """conftest `ports` 의 accounts 자리에 paper-main(auto, KR) 하나를 내는 가짜 계좌 포트."""
    acct, b = broker
    ports["accounts"] = SimpleNamespace(
        accounts=lambda: [acct],
        brokers=lambda: [b],
        market_client=lambda: None,
        policy=accounts_api.effective_mode,
        paused=accounts_api.is_paused,
        holiday_fn=lambda: None,
    )
    return ports


def _execute(port, conn, project, **over):
    kw = {
        "decision": _decision(),
        "date": D,
        "market": "KR",
        "price": Decimal("10050"),
        "exchange": None,
        "dry_run": False,
        "scalp_leg": "entry",
        "skip_accounts": frozenset(),
        "max_qty": None,
        "trade_block": None,
        "notifier": Notes(),
        "now": lambda: T0,
        "state": project / "state",
    }
    kw.update(over)
    return port.execute(conn, **kw)


def test_stock_exec_port_is_declared_and_builds(settings):
    assert [p.name for p in FEATURE.ports] == ["stock_exec"]
    port = spot_ports.build_port(SimpleNamespace(settings=settings, ports={}))
    assert isinstance(port, StockExecPortImpl)
    assert hasattr(port, "execute") and hasattr(port, "cancel") and hasattr(port, "sync_fills")


def test_execute_scalp_entry_places_limit_buy_for_paper_auto_account(
    conn, settings, project, exec_ports, broker
):
    port = StockExecPortImpl(settings, exec_ports)
    out = _execute(port, conn, project)
    # 계좌별 결과의 주문 결과는 `order` 아래(executor `_flat_order`)
    assert len(out) == 1 and out[0]["approved"] and out[0]["order"]["status"] == "accepted"
    row = conn.execute("SELECT origin, decision_id, qty FROM orders").fetchone()
    assert (row["origin"], row["decision_id"], row["qty"]) == ("scalp", None, 49)
    assert broker[1].orders[0].price == Decimal("10050")
    d = run_dir(D, "KR", "005930", project / "state")
    assert list(d.glob("57_order_scalp_entry_paper-main*"))
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_execute_skips_accounts_in_skip_set(conn, settings, project, exec_ports, broker):
    port = StockExecPortImpl(settings, exec_ports)
    out = _execute(port, conn, project, skip_accounts=frozenset({"paper-main"}))
    assert out == []
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    assert broker[1].orders == []


def _order(conn, order_no: str) -> int:
    return repo.insert_order(
        conn,
        decision_id=None,
        account_id="paper-main",
        ticker="005930",
        market="KR",
        side="BUY",
        qty=3,
        order_type="LIMIT",
        price=Decimal("10050"),
        status="accepted",
        order_no=order_no,
        message="",
        mode="auto",
        created_at="2026-10-09T00:10:00+00:00",
        origin="scalp",
    )


def test_cancel_goes_through_core_cancel(conn, settings, exec_ports, broker):
    oid = _order(conn, "0000000001")
    port = StockExecPortImpl(settings, exec_ports)
    r = port.cancel(
        conn,
        account_id="paper-main",
        order_id=oid,
        order_no="0000000001",
        market="KR",
        exchange=None,
        dry_run=False,
        reason="단타 미체결 취소",
        notifier=Notes(),
        now=lambda: T0,
    )
    assert r["ok"] and r["status"] == "cancelled"
    assert broker[1].cancelled == ["0000000001"]
    status = conn.execute("SELECT status FROM orders WHERE id=?", (oid,)).fetchone()[0]
    assert status == "cancelled"


def test_sync_fills_delegates_to_spot_fills(conn, settings, exec_ports, broker):
    _order(conn, "0000000002")
    broker[1].fills[("KR", D)] = [
        Fill(
            order_no="0000000002",
            ticker="005930",
            market="KR",
            side="BUY",
            qty=3,
            price=Decimal("10050"),
            filled_at=datetime(2026, 10, 9, 1, 5, tzinfo=UTC),
        )
    ]
    port = StockExecPortImpl(settings, exec_ports)
    r = port.sync_fills(conn, account_id="paper-main", d=D, now=lambda: T0, notifier=Notes())
    assert r["inserted"] == 1
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1


def test_sync_fills_unknown_account_returns_error(conn, settings, exec_ports):
    port = StockExecPortImpl(settings, exec_ports)
    r = port.sync_fills(conn, account_id="nope", d=D, now=lambda: T0, notifier=Notes())
    assert "error" in r
