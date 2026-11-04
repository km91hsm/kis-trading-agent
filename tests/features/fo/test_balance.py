"""Phase 26 단계 2 Task 2.7: 파생 잔고 스냅샷·대조(옛 `orchestrator/test_balance_dispatch.py` 이식).

옛 디스패치(`orchestrator.balance.refresh`·`reconcile.reconcile_all` 이 `asset` 로 갈랐다)는 새
저장소에 없다 — fo 는 `fo.balance.refresh/reconcile` 을 직접 부르고, 현물 잡은 accounts 포트의
현물 목록만 받는다(`test_spot_port_never_hands_out_fo_accounts`)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.features.accounts.conftest import make_account
from tests.features.fo.conftest import Notes
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.fo import balance, store
from trader.features.fo.settings import FoCfg
from trader.kernel.broker.errors import KisApiError
from trader.kernel.broker.fake import FakeFoBroker
from trader.kernel.broker.models import FoBalance, FoCash, FoPosition
from trader.kernel.config.secrets import Secrets
from trader.kernel.ledger import control

NOW = datetime(2026, 10, 27, 1, 30, tzinfo=UTC)
NAV = Decimal("100000000")
CFG = FoCfg(enabled=True)
STALE_AFTER_S = 600


def fo_account():
    return make_account("paper-fo", "paper", "auto", ["KR"], acnt_prdt_cd="03", asset="fo")


def fo_balance(positions=()) -> FoBalance:
    z = Decimal(0)
    cash = FoCash(
        deposit=NAV, orderable_cash=Decimal("50000000"), orderable_total=Decimal("50000000"),
        margin_total=Decimal("1000000"), add_margin=z, nav=NAV, realized_today=z, unrealized=z,
        requested_at=NOW, received_at=NOW,
    )  # fmt: skip
    return FoBalance(positions=list(positions), unverified=[], cash=cash)


def test_balance_refresh_writes_fo_snapshots(conn):
    fo = fo_account()
    fb = FakeFoBroker(fo, balance=fo_balance([FoPosition(
        code="105W11", name="105W11", kind="FUTURES", contract_side="SHORT", qty=2,
        avg_price=Decimal("350"), current_price=Decimal("351"), pnl=Decimal("-100000"),
        sellable_qty=2,
    )]), now=lambda: NOW)  # fmt: skip
    assert balance.snapshot_age(conn, fo.id, NOW) == float("inf")
    out = balance.refresh(conn, fb, fo, now=lambda: NOW, cfg=CFG)
    assert (out["account_id"], out["date"], out["positions"]) == ("paper-fo", "2026-10-27", 1)
    assert fb.calls == ["get_balance"]
    nav = conn.execute("SELECT nav, margin_total FROM fo_nav_snapshot").fetchone()
    assert (Decimal(nav["nav"]), Decimal(nav["margin_total"])) == (NAV, Decimal("1000000"))
    rows = conn.execute(
        "SELECT code, contract_side, qty, delta_notional FROM fo_positions_snapshot"
    ).fetchall()
    assert (rows[0]["code"], rows[0]["contract_side"], rows[0]["qty"]) == ("105W11", "SHORT", 2)
    assert Decimal(rows[0]["delta_notional"]) == -2 * Decimal("351") * 50000
    # 현물 스냅샷에 파생 계좌가 섞이지 않는다
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM positions_snapshot WHERE account_id='paper-fo'"
        ).fetchone()[0]
        == 0
    )
    # 방금 쓴 파생 스냅샷의 나이(옛 only_stale 판정의 근거)
    conn.execute("UPDATE fo_nav_snapshot SET created_at=?", (NOW.isoformat(),))
    conn.commit()
    assert balance.snapshot_age(conn, fo.id, NOW) < STALE_AFTER_S
    # 같은 틱에 읽은 잔고를 넘기면 다시 조회하지 않는다
    balance.refresh(conn, fb, fo, now=lambda: NOW, cfg=CFG, balance=fo_balance())
    assert fb.calls == ["get_balance"]


def test_balance_refresh_fo_error_propagates_without_snapshot(conn):
    fo = fo_account()
    fb = FakeFoBroker(fo, balance=fo_balance(), fail={"get_balance"}, now=lambda: NOW)
    with pytest.raises(KisApiError):
        balance.refresh(conn, fb, fo, now=lambda: NOW, cfg=CFG)
    assert conn.execute("SELECT COUNT(*) FROM fo_nav_snapshot").fetchone()[0] == 0


def test_reconcile_fo_uses_recover(conn):
    """fo 계좌의 대조는 `positions.recover` — 브로커에 있는데 원장에 없는 보유는 `unknown`
    (미확정)으로 보고하고 알림·control 플래그를 남긴다."""
    fo = fo_account()
    held = FoPosition(
        code="105W11", name="105W11", kind="FUTURES", contract_side="LONG", qty=2,
        avg_price=Decimal("350"), current_price=Decimal("351"), pnl=Decimal(0), sellable_qty=2,
    )  # fmt: skip
    fb = FakeFoBroker(fo, balance=fo_balance([held]), now=lambda: NOW)
    notes = Notes()
    r = balance.reconcile(conn, fb, fo, today=date(2026, 10, 27), now=lambda: NOW,
                          notifier=notes)  # fmt: skip
    assert r["market"] == "KR" and r["mismatches"], r
    assert control.get_flag(conn, "reconcile:paper-fo:KR:2026-10-27") is not None
    assert any("[대조 불일치]" in t and "paper-fo" in t and "KR(파생)" in t for t in notes.msgs)
    assert "get_balance" in fb.calls
    assert [p.status for p in store.open_positions(conn, "paper-fo")] == ["unknown"]
    assert not conn.in_transaction


def test_spot_port_never_hands_out_fo_accounts():
    """현물 잡은 `ports["accounts"].accounts()` 만 받는다 — fo 계좌는 `fo_accounts()` 쪽에만."""
    stock = make_account("paper-main", "paper", "auto", ["KR"])
    fo = fo_account()
    port = AccountsPortImpl([stock, fo], Secrets())
    assert [a.id for a in port.accounts()] == ["paper-main"]
    assert [a.id for a in port.fo_accounts()] == ["paper-fo"]
